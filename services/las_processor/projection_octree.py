"""
Octree 加速的点云投影生成器

用 slam-map 的 octree 引擎替代 cv2.projectPoints 逐点投影：
1. octree_build: LAS → 八叉树数据集（manifest.json + hierarchy.bin + pages/）
2. octree_render: 八叉树 + 相机位姿 → PPM + 深度图 → 色彩渲染图
3. depth-to-3D: 深度图 → 像素↔3D 坐标映射

相比 cv2.projectPoints:
- 八叉树视锥裁剪加速（只渲染视野内的点）
- 更高质量渲染（点大小控制、深度分层）
- 支持任意分辨率输出
"""

import json
import math
import os
import shutil
import subprocess
import time
import tempfile
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PIL import Image
import laspy

from services.las_processor.projection import _apply_camera_like_shading, _load_poses_and_offset

OCTREE_SOURCE_DIR = "/Users/pangjinfu/code/slam-map/slam-map-engine/octree"
OCTREE_BUILD_BIN = os.environ.get(
    "OCTREE_BUILD_BIN",
    str(Path(OCTREE_SOURCE_DIR) / "build" / "octree_build"),
)
OCTREE_RENDER_BIN = os.environ.get(
    "OCTREE_RENDER_BIN",
    str(Path(OCTREE_SOURCE_DIR) / "build" / "octree_render"),
)
OCTREE_CONFIG = os.environ.get(
    "OCTREE_CONFIG",
    str(Path(OCTREE_SOURCE_DIR) / "config" / "octree_render_100m.yaml"),
)

TILE_PX = 512
VIEW_RANGE = 50.0
SAMPLE_INTERVAL_M = 5.0
GRID_INTERVAL_M = 10.0  # 网格位姿间隔（每张tile覆盖~77m，10m间距保证充分重叠）
BLACK_PIXEL_THRESHOLD = 0.90
PITCH_DEG = -15.0
EULER_VIEW_DIRECTIONS = [
    ('yaw0', 0.0, PITCH_DEG, 0.0),
    ('yaw90', 90.0, PITCH_DEG, 0.0),
    ('yaw180', 180.0, PITCH_DEG, 0.0),
    ('yaw270', 270.0, PITCH_DEG, 0.0),
]


def _filter_trajectory_poses(
    poses: list[dict],
    min_time_sec: float = 1.0,
    min_dist_m: float = 2.0,
) -> list[dict]:
    """按时间和位置间隔对轨迹位姿做下采样。"""
    if not poses:
        return []

    filtered: list[dict] = [poses[0]]
    last_kept = poses[0]
    for pose in poses[1:]:
        ts = float(pose.get("ts", 0.0))
        last_ts = float(last_kept.get("ts", 0.0))
        dx = float(pose.get("x", 0.0)) - float(last_kept.get("x", 0.0))
        dy = float(pose.get("y", 0.0)) - float(last_kept.get("y", 0.0))
        dz = float(pose.get("z", 0.0)) - float(last_kept.get("z", 0.0))
        dist = math.sqrt(dx * dx + dy * dy + dz * dz)
        time_gap = ts - last_ts
        if time_gap >= min_time_sec or dist >= min_dist_m:
            filtered.append(pose)
            last_kept = pose
    return filtered


def _check_octree_binaries() -> None:
    """检查 octree 可执行文件是否存在。"""
    missing = []
    for name, path in [("octree_build", OCTREE_BUILD_BIN), ("octree_render", OCTREE_RENDER_BIN)]:
        if not os.path.exists(path):
            missing.append(f"{name}: {path}")
    if missing:
        raise FileNotFoundError(
            "缺少 octree 引擎二进制，无法执行预处理。请先在 slam-map 工程中编译 octree_build/octree_render，或设置 OCTREE_BUILD_BIN/OCTREE_RENDER_BIN。\n"
            + "\n".join(missing)
        )


def _resolve_pdal_binary() -> Optional[str]:
    """从 PATH、环境变量和常见安装路径中解析 PDAL 可执行文件。"""
    candidates: list[str] = []
    env_bin = os.environ.get("PDAL_BIN")
    if env_bin:
        candidates.append(env_bin)

    resolved = shutil.which("pdal")
    if resolved:
        candidates.append(resolved)

    candidates.extend([
        "/opt/homebrew/bin/pdal",
        "/usr/local/bin/pdal",
        "/opt/homebrew/Caskroom/miniconda/base/bin/pdal",
        "/usr/bin/pdal",
        "/bin/pdal",
    ])

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        path = Path(candidate)
        if path.exists() and os.access(path, os.X_OK):
            return str(path)
    return None


def _normalize_intensity_in_las(las_path: str | Path) -> None:
    """将 LAS 文件的 intensity 拉伸到 0-65535 范围。
    
    octree_render 使用 intensity 着色，但原始 intensity 值偏低（均值 ~30/255），
    导致渲染图近乎全黑。此函数读取 LAS，将 intensity 线性拉伸到完整的 uint16 范围。
    
    使用 laspy 原地修改再写回，保留原始 LAS header 版本和点格式。
    """
    las_path = Path(las_path)
    if not las_path.exists():
        return
    try:
        in_las = laspy.read(las_path)
    except Exception:
        return
    if not hasattr(in_las, 'intensity') or in_las.intensity.size == 0:
        return
    i = in_las.intensity.astype(np.float64)
    i_min, i_max = float(i.min()), float(i.max())
    
    if i_max - i_min < 1.0:
        in_las.intensity = np.full_like(in_las.intensity, 32768, dtype=np.uint16)
    else:
        i_norm = (i - i_min) / (i_max - i_min) * 65535.0
        in_las.intensity = np.clip(np.round(i_norm), 0, 65535).astype(np.uint16)
    
    tmp = las_path.with_suffix(las_path.suffix + ".tmp")
    in_las.write(tmp)
    tmp.replace(las_path)
    print(f"[OCTREE] intensity 归一化完成: {las_path.name}  ({i_min:.0f}→{i_max:.0f} → 0→65535)")


def _downsample_las_with_laspy(
    source_path: Path,
    out_path: Path,
    voxel_size_m: float = 0.02,
) -> None:
    """使用 laspy 做一个简单的空间下采样，作为 PDAL 的降级方案。"""
    print(f"[OCTREE] 使用 laspy 降级下采样 LAS: {source_path} -> {out_path}")
    in_las = laspy.read(source_path)
    header = in_las.header
    points = np.column_stack([
        in_las.x,
        in_las.y,
        in_las.z,
    ])

    if len(points) == 0:
        raise RuntimeError("LAS 文件中没有点云数据")

    scale = np.floor(points / voxel_size_m).astype(np.int64)
    unique = np.unique(scale, axis=0)
    keep_idx = []
    for cell in unique:
        mask = np.all(scale == cell, axis=1)
        if np.any(mask):
            keep_idx.append(int(np.argmax(mask)))

    keep_idx_array = np.array(keep_idx, dtype=np.int64)
    if keep_idx_array.size == 0:
        raise RuntimeError("LAS 降采样后没有保留任何点")

    selected = in_las[keep_idx_array]
    header = laspy.LasHeader(point_format=3, version="1.2")
    header.scale = in_las.header.scale
    header.offset = in_las.header.offset
    out_las = laspy.LasData(header)
    out_las.points = selected.points
    out_las.write(out_path)


def _prepare_downsampled_las(
    las_path: str,
    output_dir: str,
    force: bool = False,
    voxel_size_m: float = 0.02,
) -> str:
    """在 octree_build 前对 LAS 做体素下采样。优先尝试 PDAL，失败时回退到 laspy。"""
    source_path = Path(las_path)
    if not source_path.exists():
        raise FileNotFoundError(f"LAS 文件不存在: {las_path}")

    out_dir = Path(output_dir) / "downsampled_las"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{source_path.stem}_downsampled.las"

    if out_path.exists() and not force:
        print(f"[OCTREE] 使用已有下采样 LAS: {out_path}")
        return str(out_path)

    pdal_bin = _resolve_pdal_binary()
    if pdal_bin:
        cmd = [
            pdal_bin,
            "translate",
            str(source_path),
            str(out_path),
            "filters.voxeldownsize",
            f"--filters.voxeldownsize.cell={voxel_size_m}",
            "--writers.las.dataformat_id=3",   # 强制写 format 3 以兼容 octree_build（不支持 format 4+）
        ]
        print(f"[OCTREE] 使用 PDAL 下采样 LAS: {source_path} -> {out_path}")
        print(f"[OCTREE]   PDAL 路径: {pdal_bin}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode == 0:
            _normalize_intensity_in_las(out_path)
            return str(out_path)
        print(f"[OCTREE] PDAL 降采样失败，回退到 laspy: {result.stderr[:500]}")

    _downsample_las_with_laspy(source_path, out_path, voxel_size_m=voxel_size_m)
    _normalize_intensity_in_las(out_path)
    return str(out_path)


def build_octree(
    las_path: str,
    output_dir: str,
    offset_xyz: tuple[float, float, float] = (0, 0, 0),
    force: bool = False,
) -> str:
    """
    用 octree_build 将 LAS 点云构建为八叉树数据集。
    
    返回: octree 数据集目录路径
    """
    _check_octree_binaries()

    octree_dir = Path(output_dir) / "octree_data"
    manifest_path = octree_dir / "manifest.json"
    
    if manifest_path.exists() and not force:
        print(f"[OCTREE] 八叉树数据集已存在: {octree_dir}")
        return str(octree_dir)
    
    octree_dir.mkdir(parents=True, exist_ok=True)
    
    offset_str = f"[{int(offset_xyz[0])},{int(offset_xyz[1])},{int(offset_xyz[2])}]"
    
    cmd = [
        OCTREE_BUILD_BIN,
        "--config", OCTREE_CONFIG,
        "--input", las_path,
        "--output", str(octree_dir),
        "--position-offset", offset_str,
        "--log-level", "info",
    ]
    
    print(f"[OCTREE] 构建八叉树: {las_path}")
    print(f"[OCTREE]   输出: {octree_dir}")
    print(f"[OCTREE]   偏移: {offset_str}")
    t0 = time.time()
    
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    
    if result.returncode != 0:
        # octree_build 的输出可能写入日志文件而非 stderr，附加读取日志兜底
        build_log_path = octree_dir / "logs" / "octree_build.log"
        log_tail = ""
        if build_log_path.exists():
            try:
                with open(build_log_path) as lf:
                    lines = lf.readlines()
                log_tail = "\n".join(lines[-20:])  # 取最后 20 行
            except Exception as exc:
                log_tail = f"(读取日志失败: {exc})"
        raise RuntimeError(
            f"octree_build 失败 (code={result.returncode}):\n"
            f"stderr: {result.stderr[:500]}\n"
            f"--- octree_build.log tail (最后20行) ---\n"
            f"{log_tail}"
        )
    
    elapsed = time.time() - t0
    print(f"[OCTREE] 构建完成: {elapsed:.1f}s")
    
    return str(octree_dir)


def render_pose_octree(
    octree_dataset: str,
    colmap_line: str,
    image_width: int,
    image_height: int,
    focal_norm: float,
    color_output: str,
    depth_raw_output: Optional[str] = None,
    config: Optional[str] = None,
) -> bool:
    """
    用 octree_render --colmap 渲染一个位姿的视图。
    
    返回: True 成功 / False 失败
    """
    _check_octree_binaries()

    cmd = [
        OCTREE_RENDER_BIN,
        "--dataset", octree_dataset,
        "--colmap", colmap_line,
        "--image-width", str(image_width),
        "--image-height", str(image_height),
        "--focal-normalized", str(focal_norm),
        "--color-output", color_output,
    ]
    
    if depth_raw_output:
        cmd.extend(["--depth-raw-output", depth_raw_output])
    if config:
        cmd.extend(["--config", config])
    else:
        cmd.extend(["--config", OCTREE_CONFIG])
    
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    
    if result.returncode != 0:
        print(f"[OCTREE] 渲染失败: {result.stderr[:200]}")
        return False
    
    return True


def _rotmat_to_quat(R: np.ndarray) -> tuple[float, float, float, float]:
    """3x3 旋转矩阵 → 四元数 (qw, qx, qy, qz)"""
    tr = R[0, 0] + R[1, 1] + R[2, 2]
    if tr > 0:
        s = 2.0 * np.sqrt(tr + 1.0)
        qw = 0.25 * s
        qx = (R[2, 1] - R[1, 2]) / s
        qy = (R[0, 2] - R[2, 0]) / s
        qz = (R[1, 0] - R[0, 1]) / s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = 2.0 * np.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2])
        qw = (R[2, 1] - R[1, 2]) / s
        qx = 0.25 * s
        qy = (R[0, 1] + R[1, 0]) / s
        qz = (R[0, 2] + R[2, 0]) / s
    elif R[1, 1] > R[2, 2]:
        s = 2.0 * np.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2])
        qw = (R[0, 2] - R[2, 0]) / s
        qx = (R[0, 1] + R[1, 0]) / s
        qy = 0.25 * s
        qz = (R[1, 2] + R[2, 1]) / s
    else:
        s = 2.0 * np.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1])
        qw = (R[1, 0] - R[0, 1]) / s
        qx = (R[0, 2] + R[2, 0]) / s
        qy = (R[1, 2] + R[2, 1]) / s
        qz = 0.25 * s
    return _normalize_quat(qw, qx, qy, qz)


def _look_at_colmap_quat(forward_world: np.ndarray, up_world: np.ndarray = None) -> tuple:
    """
    给定世界坐标下的相机前方方向和上方方向，计算 COLMAP 四元数 (world→camera)。

    COLMAP 相机坐标系: X=右, Y=下, Z=前（看向场景方向）
    R_wc: world → camera
    """
    if up_world is None:
        up_world = np.array([0.0, 0.0, 1.0])  # Z-up 世界坐标系

    fwd = forward_world / (np.linalg.norm(forward_world) + 1e-10)
    right = np.cross(fwd, up_world)
    right_norm = np.linalg.norm(right)
    if right_norm < 1e-6:
        # forward 和 up 平行，换一个 up
        up_world = np.array([0.0, 1.0, 0.0])
        right = np.cross(fwd, up_world)
        right_norm = np.linalg.norm(right)
    right = right / (right_norm + 1e-10)
    down = np.cross(fwd, right)  # COLMAP Y 轴向下

    # R_wc: 行是相机轴在世界坐标中的方向
    # R_wc = [right; down; forward] (每行是一个相机轴)
    R_wc = np.array([right, down, fwd], dtype=np.float64)
    return _rotmat_to_quat(R_wc)


def _build_colmap_line(pose: dict, offset_xyz: tuple[float, float, float], z_bias: float = 0.0,
                       heading_deg: float = 0.0, pitch_deg: float = 0.0, roll_deg: float = 0.0) -> str:
    """
    从位姿字典构建 octree_render 需要的 colmap 行。

    现在优先使用轨迹位姿 + Euler 角生成朝向；当位姿自带四元数且没有明确的 Euler 参数时，兼容使用原始四元数。
    """
    tx = pose['x']
    ty = pose['y']
    tz = pose['z'] + z_bias

    qw = pose.get('qw', 1.0)
    qx = pose.get('qx', 0.0)
    qy = pose.get('qy', 0.0)
    qz = pose.get('qz', 0.0)

    use_euler = abs(float(pose.get('yaw_deg', 0.0))) > 1e-9 or abs(float(pose.get('pitch_deg', 0.0))) > 1e-9 or abs(float(pose.get('roll_deg', 0.0))) > 1e-9
    if use_euler:
        yaw_deg = float(pose.get('yaw_deg', heading_deg))
        pitch_deg = float(pose.get('pitch_deg', pitch_deg))
        roll_deg = float(pose.get('roll_deg', roll_deg))
        import math
        yaw_rad = math.radians(yaw_deg)
        pitch_rad = math.radians(pitch_deg)
        roll_rad = math.radians(roll_deg)

        horiz_x = math.sin(yaw_rad)
        horiz_y = math.cos(yaw_rad)
        fwd = np.array([
            horiz_x * math.cos(pitch_rad),
            horiz_y * math.cos(pitch_rad),
            math.sin(pitch_rad),
        ])
        fwd = fwd / (np.linalg.norm(fwd) + 1e-10)

        if abs(roll_rad) > 1e-9:
            up_world = np.array([0.0, 0.0, 1.0])
            right = np.cross(fwd, up_world)
            right_norm = np.linalg.norm(right)
            if right_norm < 1e-6:
                up_world = np.array([0.0, 1.0, 0.0])
                right = np.cross(fwd, up_world)
                right_norm = np.linalg.norm(right)
            right = right / (right_norm + 1e-10)
            down = np.cross(fwd, right)
            right_rot = right * math.cos(roll_rad) + down * math.sin(roll_rad)
            down_rot = -right * math.sin(roll_rad) + down * math.cos(roll_rad)
            R_wc = np.array([right_rot, down_rot, fwd], dtype=np.float64)
            qw, qx, qy, qz = _rotmat_to_quat(R_wc)
        else:
            qw, qx, qy, qz = _look_at_colmap_quat(fwd)
    elif abs(qw - 1.0) < 0.01 and abs(qx) < 0.01 and abs(qy) < 0.01 and abs(qz) < 0.01:
        import math
        yaw_rad = math.radians(heading_deg)
        pitch_rad = math.radians(pitch_deg)
        horiz_x = math.sin(yaw_rad)
        horiz_y = math.cos(yaw_rad)
        fwd = np.array([
            horiz_x * math.cos(pitch_rad),
            horiz_y * math.cos(pitch_rad),
            math.sin(pitch_rad),
        ])
        qw, qx, qy, qz = _look_at_colmap_quat(fwd)

    return f"{qw:.10f} {qx:.10f} {qy:.10f} {qz:.10f} {tx:.6f} {ty:.6f} {tz:.6f}"


def _quat_to_rotmat_colmap(qw: float, qx: float, qy: float, qz: float) -> np.ndarray:
    """
    COLMAP 四元数 → 旋转矩阵 (world→camera)。
    参数顺序 qw, qx, qy, qz，与 slam-map 的 batch_render_octree_colmap_direct.py 一致。
    """
    n = np.sqrt(qw * qw + qx * qx + qy * qy + qz * qz)
    if n <= 0:
        return np.eye(3, dtype=np.float32)
    qw /= n; qx /= n; qy /= n; qz /= n
    return np.array([
        [1.0 - 2.0 * (qy * qy + qz * qz), 2.0 * (qx * qy - qw * qz), 2.0 * (qx * qz + qw * qy)],
        [2.0 * (qx * qy + qw * qz), 1.0 - 2.0 * (qx * qx + qz * qz), 2.0 * (qy * qz - qw * qx)],
        [2.0 * (qx * qz - qw * qy), 2.0 * (qy * qz + qw * qx), 1.0 - 2.0 * (qx * qx + qy * qy)],
    ], dtype=np.float32)


def _quat_mul(a, b):
    """四元数乘法"""
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (
        aw*bw - ax*bx - ay*by - az*bz,
        aw*bx + ax*bw + ay*bz - az*by,
        aw*by - ax*bz + ay*bw + az*bx,
        aw*bz + ax*by - ay*bx + az*bw,
    )

def _quat_from_axis_angle(axis, angle_deg):
    """轴角 → 四元数"""
    import math
    half = math.radians(angle_deg) * 0.5
    c, s = math.cos(half), math.sin(half)
    if axis == 'z': return (c, 0, 0, s)
    if axis == 'x': return (c, s, 0, 0)
    return (c, 0, s, 0)  # y axis

def _normalize_quat(qw: float, qx: float, qy: float, qz: float) -> tuple[float, float, float, float]:
    """归一化四元数"""
    import math
    n = math.sqrt(qw * qw + qx * qx + qy * qy + qz * qz)
    if n <= 0:
        return 1.0, 0.0, 0.0, 0.0
    return qw / n, qx / n, qy / n, qz / n

def _yaw_quat_deg(yaw_deg: float) -> tuple[float, float, float, float]:
    """偏航角 → 四元数"""
    import math
    half = math.radians(yaw_deg) * 0.5
    return math.cos(half), 0.0, 0.0, math.sin(half)

def _axis_angle_quat(axis: str, angle_deg: float) -> tuple[float, float, float, float]:
    """轴角 → 四元数"""
    import math
    half = math.radians(angle_deg) * 0.5
    c = math.cos(half)
    s = math.sin(half)
    axis_lower = axis.lower()
    if axis_lower == "x":
        return c, s, 0.0, 0.0
    if axis_lower == "y":
        return c, 0.0, s, 0.0
    return c, 0.0, 0.0, s

def _rotate_colmap_line(colmap_line: str, axis: str = 'z', angle_deg: float = 90.0) -> str:
    """旋转 colmap 行的四元数（绕指定轴旋转指定角度）"""
    parts = colmap_line.split()
    qw, qx, qy, qz = float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3])
    rotate_q = _axis_angle_quat(axis, angle_deg)
    rw, rx, ry, rz = _quat_mul(rotate_q, (qw, qx, qy, qz))
    rw, rx, ry, rz = _normalize_quat(rw, rx, ry, rz)
    tx, ty, tz = parts[4], parts[5], parts[6]
    return f"{rw:.10f} {rx:.10f} {ry:.10f} {rz:.10f} {tx} {ty} {tz}"



def _depth_to_xyz_map(
    depth: np.ndarray,
    fx: float, fy: float, cx: float, cy: float,
    qw: float, qx: float, qy: float, qz: float,
    tx: float, ty: float, tz: float,
    offset_xyz: tuple[float, float, float],
) -> tuple[dict, np.ndarray]:
    """
    深度图 → 像素↔3D世界坐标映射。
    
    直接复用 slam-map batch_render_octree_colmap_direct.py 的 _depth_to_world_xyz：
    - R_wc = _quat_to_rotmat_colmap(qw, qx, qy, qz)  # world→camera
    - X_world = (X_cam - t_wc) @ R_wc
    
    返回: (coord_map, world_array)
    - coord_map: dict, 只包含有效像素的坐标映射
    - world_array: (h, w, 3) float32 数组，与 slam-map 的 NPY 格式一致，无效像素为 [0,0,0]
    """
    h, w = depth.shape
    R = _quat_to_rotmat_colmap(qw, qx, qy, qz)  # world→camera
    
    xs = np.arange(w, dtype=np.float32)
    ys = np.arange(h, dtype=np.float32)
    grid_x, grid_y = np.meshgrid(xs, ys)
    
    z = depth.astype(np.float32, copy=False)
    valid = np.isfinite(z) & (z > 0)
    
    x_cam = ((grid_x - cx) / fx) * z
    y_cam = ((grid_y - cy) / fy) * z
    cam_points = np.stack([x_cam, y_cam, z], axis=-1)
    
    # COLMAP: X_cam = R_wc * (X_world - t_wc)
    # 逆变换: X_world = R_wc^T * (X_cam - t_wc)
    # 行向量版本: X_world_row = (X_cam_row - t_wc_row) @ R_wc
    t = np.array([tx, ty, tz], dtype=np.float32)
    world = (cam_points.reshape(-1, 3) - t) @ R.astype(np.float32)
    world = world.reshape(h, w, 3)
    
    # tx/ty/tz 已经是局部坐标（减去了 offset），所以 world 也是局部坐标
    # 不需要再减 offset！之前错误地双重减去了 offset，导致坐标不匹配
    
    # 无效像素设为 [0,0,0]（与 slam-map NPY 格式一致）
    world[~valid] = 0.0
    
    # 构建 coord_map（只保留有有效深度的像素）
    coord_map = {}
    valid_mask = valid.reshape(-1)
    world_flat = world.reshape(-1, 3)
    
    for i in range(w * h):
        if not valid_mask[i]:
            continue
        px = i % w
        py = i // w
        key = f"{px},{py}"
        coord_map[key] = [
            float(world_flat[i, 0]),
            float(world_flat[i, 1]),
            float(world_flat[i, 2]),
        ]
    
    return coord_map, world


def prepare_octree_render_plan(
    poses: list[dict],
    output_dir: str = "projections",
    sample_interval_m: float = SAMPLE_INTERVAL_M,
    max_poses: Optional[int] = None,
    grid_interval_m: float = 10.0,
    use_grid_sampling: bool = True,
) -> tuple[Path, list[dict]]:
    """
    生成 octree_render 的投影位姿计划。

    该阶段只负责把轨迹位姿和网格位姿展开为可渲染的视角列表，
    后续渲染阶段再基于这些视角调用 octree_render 生成图像。
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pose_file = out_dir / "projection_view_poses.json"

    pose_candidates = poses
    if max_poses is not None:
        pose_candidates = pose_candidates[: max_poses]

    sampled = pose_candidates

    if use_grid_sampling and poses:
        # 网格覆盖范围：优先用点云边界（octree manifest），fallback 到轨迹范围
        manifest_path = Path(output_dir) / "octree_data" / "manifest.json"
        if manifest_path.exists():
            with open(manifest_path) as f:
                manifest = json.load(f)
            xmin_pc, ymin_pc, _ = manifest["root_min"]
            xmax_pc, ymax_pc, _ = manifest["root_max"]
            # 缩进 margin 避免边缘空白
            margin = grid_interval_m * 1.5
            x_min = xmin_pc + margin
            x_max = xmax_pc - margin
            y_min = ymin_pc + margin
            y_max = ymax_pc - margin
            print(f"[OCTREE] 网格范围: 点云边界 X[{x_min:.0f},{x_max:.0f}] Y[{y_min:.0f},{y_max:.0f}]")
        else:
            xs_all = np.array([p["x"] for p in poses], dtype=np.float64)
            ys_all = np.array([p["y"] for p in poses], dtype=np.float64)
            margin = grid_interval_m * 3
            x_min = xs_all.min() - margin
            x_max = xs_all.max() + margin
            y_min = ys_all.min() - margin
            y_max = ys_all.max() + margin
            print(f"[OCTREE] 网格范围: 轨迹+margin X[{x_min:.0f},{x_max:.0f}] Y[{y_min:.0f},{y_max:.0f}]")

        zs = np.array([p["z"] for p in poses], dtype=np.float64)
        z_mean = zs.mean()

        grid_x = np.arange(x_min, x_max, grid_interval_m)
        grid_y = np.arange(y_min, y_max, grid_interval_m)

        grid_poses = []
        for gx in grid_x:
            for gy in grid_y:
                grid_poses.append({
                    "x": float(gx),
                    "y": float(gy),
                    "z": float(z_mean),
                    "qx": 0.0,
                    "qy": 0.0,
                    "qz": 0.0,
                    "qw": 1.0,
                    "name": f"grid_{gx:.1f}_{gy:.1f}",
                })

        print(f"[OCTREE] 轨迹位姿: {len(sampled)} 个, 网格位姿: {len(grid_poses)} 个 ({len(grid_x)}x{len(grid_y)})")
        sampled.extend(grid_poses)

    views = []
    for pose in sampled:
        for view_dir, yaw_deg, pitch_deg, roll_deg in EULER_VIEW_DIRECTIONS:
            views.append({
                "name": pose.get("name", "pose"),
                "view_dir": view_dir,
                "heading_deg": yaw_deg,
                "yaw_deg": float(yaw_deg),
                "pitch_deg": float(pitch_deg),
                "roll_deg": float(roll_deg),
                "x": float(pose["x"]),
                "y": float(pose["y"]),
                "z": float(pose["z"]),
                "qx": float(pose.get("qx", 0.0)),
                "qy": float(pose.get("qy", 0.0)),
                "qz": float(pose.get("qz", 0.0)),
                "qw": float(pose.get("qw", 1.0)),
            })

    payload = {
        "sample_interval_m": float(sample_interval_m),
        "grid_interval_m": float(grid_interval_m),
        "use_grid_sampling": use_grid_sampling,
        "count": len(sampled),
        "views": views,
    }
    with open(pose_file, "w") as f:
        json.dump(payload, f, indent=2)
    return pose_file, views


def build_projection_view_poses(
    poses: list[dict],
    output_dir: str = "projections",
    sample_interval_m: float = SAMPLE_INTERVAL_M,
    max_poses: Optional[int] = None,
    grid_interval_m: float = 10.0,
    use_grid_sampling: bool = True,
) -> Path:
    """兼容旧接口：返回投影位姿文件路径。"""
    pose_file, _ = prepare_octree_render_plan(
        poses,
        output_dir=output_dir,
        sample_interval_m=sample_interval_m,
        max_poses=max_poses,
        grid_interval_m=grid_interval_m,
        use_grid_sampling=use_grid_sampling,
    )
    return pose_file


def _is_black_or_nearly_black(image_path: str, black_ratio_threshold: float = BLACK_PIXEL_THRESHOLD) -> bool:
    """过滤全黑或 90% 黑色的图像。"""
    if not os.path.exists(image_path):
        return True
    try:
        with Image.open(image_path) as img:
            arr = np.array(img.convert("RGB"), dtype=np.uint8)
    except Exception:
        return True
    if arr.size == 0:
        return True
    gray = np.mean(arr, axis=2)
    black_ratio = np.mean(gray < 16)
    return black_ratio >= black_ratio_threshold


def _load_z_bias(las_dir: str) -> float:
    """从 map_config.json 加载 z_bias = rtk_external_param[2] + 3.0"""
    map_path = Path(las_dir) / "map_config.json"
    if not map_path.exists():
        return 3.0
    with open(map_path) as f:
        cfg = json.load(f)
    rtk_external = cfg.get("rtk_external_param", [0.0, 0.0, 0.0])
    z_bias = float(rtk_external[2]) if isinstance(rtk_external, list) and len(rtk_external) >= 3 else 0.0
    z_lift = float(os.environ.get("FALLBACK_Z_LIFT", "3.0"))
    return z_bias + z_lift


def project_las_multi_view_octree(
    las_path: str,
    output_dir: str = "projections",
    max_poses: int = 50,
    force_rebuild: bool = False,
    render_width: int = TILE_PX,
    render_height: int = TILE_PX,
    progress_callback=None,
) -> list[dict]:
    """
    用 Octree 引擎的多视角投影生成器。
    - 构建八叉树（仅首次）
    - 对每个位姿用 octree_render 渲染
    - 生成 coord_*.json 像素↔3D 映射
    - 输出兼容旧版 tile_index.json 格式
    
    返回: generated tiles list
    """
    las_dir = str(Path(las_path).parent)
    
    poses, offset_x, offset_y, offset_z = _load_poses_and_offset("las")
    offset_xyz = (offset_x, offset_y, offset_z)
    
    if not poses:
        poses, offset_x, offset_y, offset_z = _load_poses_and_offset(las_dir)
        offset_xyz = (offset_x, offset_y, offset_z)

    if poses:
        poses = _filter_trajectory_poses(poses, min_time_sec=1.0, min_dist_m=4.0)
    
    z_bias = _load_z_bias("las")
    if z_bias == 0.0:
        z_bias = _load_z_bias(las_dir)
    print(f"[OCTREE] z_bias = {z_bias:.2f} (rtk_external + lift)")
    
    try:
        _check_octree_binaries()
    except FileNotFoundError as exc:
        raise RuntimeError(str(exc)) from exc
    
    # 1. 先对 LAS 做 0.02m 下采样，再构建八叉树
    if progress_callback:
        progress_callback("下采样 LAS 数据...", 8)
    sampled_las_path = _prepare_downsampled_las(las_path, output_dir, force=force_rebuild)

    if progress_callback:
        progress_callback("构建 Octree 八叉树...", 10)
    octree_dataset = build_octree(sampled_las_path, output_dir, offset_xyz, force=force_rebuild)
    
    if progress_callback:
        progress_callback("加载位姿数据...", 20)
    
    # 2. 加载位姿
    pose_file = Path(output_dir) / "projection_view_poses.json"
    if not poses:
        if pose_file.exists():
            print(f"[OCTREE] 无轨迹位姿，从已有投影位姿文件获取边界并重新生成网格位姿")
            with open(pose_file) as f:
                existing_data = json.load(f)
            existing_views = existing_data.get('views', [])
            if existing_views:
                xs = [v['x'] for v in existing_views]
                ys = [v['y'] for v in existing_views]
                zs = [v['z'] for v in existing_views]
                x_min, x_max = min(xs), max(xs)
                y_min, y_max = min(ys), max(ys)
                z_mean = sum(zs) / len(zs)
                
                grid_interval_m = GRID_INTERVAL_M
                margin = grid_interval_m * 1.5
                grid_x = np.arange(x_min - margin, x_max + margin, grid_interval_m)
                grid_y = np.arange(y_min - margin, y_max + margin, grid_interval_m)
                
                poses = []
                for gx in grid_x:
                    for gy in grid_y:
                        poses.append({
                            "x": float(gx),
                            "y": float(gy),
                            "z": float(z_mean),
                            "qx": 0.0,
                            "qy": 0.0,
                            "qz": 0.0,
                            "qw": 1.0,
                            "name": f"grid_{gx:.1f}_{gy:.1f}",
                        })
                print(f"[OCTREE] 从已有边界生成网格位姿: {len(poses)} 个")
                pose_file, _ = prepare_octree_render_plan(
                    poses,
                    output_dir=output_dir,
                    sample_interval_m=GRID_INTERVAL_M,
                    max_poses=None,
                    grid_interval_m=GRID_INTERVAL_M,
                    use_grid_sampling=False,
                )
            else:
                raise RuntimeError("无可用位姿")
        else:
            raise RuntimeError("无可用位姿")
    else:
        if max_poses is None or max_poses <= 0:
            selected_poses = poses
        else:
            selected_poses = poses[:min(max_poses, len(poses))]
        pose_file, _ = prepare_octree_render_plan(
            selected_poses,
            output_dir=output_dir,
            sample_interval_m=SAMPLE_INTERVAL_M,
            max_poses=max_poses,
            grid_interval_m=GRID_INTERVAL_M,
            use_grid_sampling=True,
        )
    
    # 读取位姿文件获取完整位姿列表（包含网格位姿）
    with open(pose_file) as f:
        pose_data = json.load(f)
    total_poses = pose_data.get("count", 0)
    print(f"[OCTREE] 位姿总数: {total_poses} 个")
    print(f"[OCTREE] 投影位姿文件: {pose_file}")
    
    # 使用完整的位姿列表进行渲染（去重：同一个坐标只渲染一次）
    all_poses = []
    seen_coords = set()
    for view in pose_data.get("views", []):
        key = (
            round(view["x"], 1),
            round(view["y"], 1),
            view.get("yaw_deg", view.get("heading_deg", 0.0)),
            view.get("pitch_deg", PITCH_DEG),
            view.get("roll_deg", 0.0),
        )
        if key not in seen_coords:
            seen_coords.add(key)
            all_poses.append(view)
    
    # 3. 对每个位姿渲染 3 个视角 (front/side/top)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    tile_dir = out / "tiles"
    tile_dir.mkdir(parents=True, exist_ok=True)
    
    # 清理旧投影产物，避免上一次结果污染本次生成
    for pattern in ["view_*.png", "view_*.npy", "coord_*.json", "render_color*.ppm", "render_depth*.pgm"]:
        for f in out.glob(pattern):
            if f.exists():
                f.unlink()
    for pattern in ["view_*.png", "view_*.npy", "coord_*.json"]:
        for f in tile_dir.glob(pattern):
            if f.exists():
                f.unlink()
    
    generated = []
    tile_dir = out / "tiles"
    tile_dir.mkdir(parents=True, exist_ok=True)
    
    # 焦距计算（与旧版保持一致）
    fov_deg = 75
    f = max(render_width, render_height) / (2 * np.tan(np.deg2rad(fov_deg / 2)))
    focal_norm = f / max(render_width, render_height)
    fx = fy = f
    cx = (render_width - 1) / 2.0
    cy = (render_height - 1) / 2.0
    
    t0_total = time.time()
    
    total_renders = len(all_poses)
    current_render = 0
    
    for pi, view in enumerate(all_poses):
        pose = {
            "x": view["x"],
            "y": view["y"],
            "z": view["z"],
            "qx": view.get("qx", 0.0),
            "qy": view.get("qy", 0.0),
            "qz": view.get("qz", 0.0),
            "qw": view.get("qw", 1.0),
            "yaw_deg": view.get("yaw_deg", view.get("heading_deg", 0.0)),
            "pitch_deg": view.get("pitch_deg", PITCH_DEG),
            "roll_deg": view.get("roll_deg", 0.0),
        }
        vd = view["view_dir"]
        heading_deg = view.get("heading_deg", view.get("yaw_deg", 0.0))
        fx_str = f"{pose['x']:.1f}_{pose['y']:.1f}"
        
        current_render += 1
        if progress_callback:
            render_progress = 20 + int(50 * current_render / total_renders)
            progress_callback(f"渲染投影图 {current_render}/{total_renders}...", render_progress)
        t0 = time.time()

        # 构建 colmap 行：heading 和 pitch 直接传入（网格位姿用 look-at 计算朝向）
        render_line = _build_colmap_line(
            pose,
            offset_xyz,
            z_bias,
            heading_deg,
            pose.get("pitch_deg", PITCH_DEG),
            pose.get("roll_deg", 0.0),
        )

        # 每个视角的输出路径，统一放进 tiles/ 目录，和图像产物绑定
        fname = f"view_{vd}_{fx_str}_{pi}.png"
        img_path = str(tile_dir / fname)
        npy_path = img_path.replace(".png", ".npy")

        # 如果已存在且不强制重建，跳过
        if os.path.exists(img_path) and os.path.exists(npy_path) and not force_rebuild:
            generated.append({
                "image_path": img_path,
                "npy_path": npy_path,
                "width": render_width,
                "height": render_height,
                "view": vd,
                "tile": fx_str,
                "pixel_count": 0,
            })
            continue

        with tempfile.TemporaryDirectory(prefix="octree_render_") as tmpdir:
            color_ppm = os.path.join(tmpdir, "color.ppm")
            depth_raw = os.path.join(tmpdir, "depth.raw")

            # 渲染
            ok = render_pose_octree(
                octree_dataset, render_line,
                render_width, render_height, focal_norm,
                color_ppm, depth_raw,
            )
            if not ok:
                print(f"[OCTREE] 跳过 {vd} pose#{pi}")
                continue

            if not os.path.exists(color_ppm):
                print(f"[OCTREE] 无输出: {vd} pose#{pi}")
                print(f"[OCTREE]   colmap_line: {render_line}")
                continue

            # 检查输出文件大小
            color_size = os.path.getsize(color_ppm) if os.path.exists(color_ppm) else 0
            depth_size = os.path.getsize(depth_raw) if os.path.exists(depth_raw) else 0
            print(f"[OCTREE] {vd} pose#{pi} 输出: color={color_size} bytes, depth={depth_size} bytes")
            print(f"[OCTREE]   colmap_line: {render_line}")

            # 颜色图 → PNG，并做轻微的相机式明暗增强
            with Image.open(color_ppm) as img:
                color_img = np.array(img.convert("RGB"))
                if os.path.exists(depth_raw):
                    depth = np.fromfile(depth_raw, dtype=np.float32)
                    if depth.size == render_width * render_height:
                        depth = depth.reshape(render_height, render_width)
                        color_img = _apply_camera_like_shading(color_img, depth=depth)
                else:
                    color_img = _apply_camera_like_shading(color_img)
                Image.fromarray(color_img).save(img_path, quality=95)

            # 深度 → NPY（与 slam-map 格式一致：(h, w, 3) float32，无效像素为 [0,0,0]）
            if os.path.exists(depth_raw):
                depth = np.fromfile(depth_raw, dtype=np.float32)
                expected = render_width * render_height
                if depth.size == expected:
                    depth = depth.reshape(render_height, render_width)

                    rl_parts = render_line.split()
                    rl_qw, rl_qx, rl_qy, rl_qz = float(rl_parts[0]), float(rl_parts[1]), float(rl_parts[2]), float(rl_parts[3])
                    rl_tx, rl_ty, rl_tz = float(rl_parts[4]), float(rl_parts[5]), float(rl_parts[6])

                    _, world_array = _depth_to_xyz_map(
                        depth, fx, fy, cx, cy,
                        rl_qw, rl_qx, rl_qy, rl_qz,
                        rl_tx, rl_ty, rl_tz,
                        offset_xyz,
                    )

                    npy_path = img_path.replace(".png", ".npy")
                    np.save(npy_path, world_array.astype(np.float32))

                    pixel_count = int(np.count_nonzero(np.linalg.norm(world_array, axis=2)))
                else:
                    print(f"[OCTREE] 深度尺寸不符: {depth.size}/{expected}")
                    pixel_count = 0
            else:
                pixel_count = 0

        elapsed = time.time() - t0
        print(f"[OCTREE] {vd} pose#{pi}: {elapsed:.1f}s, {pixel_count}像素")

        if _is_black_or_nearly_black(img_path):
            pixel_count = 0
            if os.path.exists(coord_path):
                os.remove(coord_path)
            if os.path.exists(img_path):
                os.remove(img_path)
            img_path = ""
            coord_path = ""
            print(f"[OCTREE] 过滤低质量图像 {fname}")

        generated.append({
            "image_path": img_path,
            "npy_path": npy_path,
            "width": render_width,
            "height": render_height,
            "view": vd,
            "tile": fx_str,
            "pixel_count": pixel_count,
            "accepted": pixel_count > 0 and not _is_black_or_nearly_black(img_path),
        })
    
    # 4. 保存 tile_index.json
    with open(str(out / "tile_index.json"), "w") as f:
        json.dump(generated, f, indent=2)
    
    total_time = time.time() - t0_total
    print(f"[OCTREE] 总耗时: {total_time:.1f}s, 生成 {len(generated)} tiles")
    
    return generated


# 兼容旧接口
project_las_to_image = project_las_multi_view_octree
