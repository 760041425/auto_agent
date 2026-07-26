"""
端到端视觉定位服务

流程：
1. 加载 COLMAP 数据（3D点云 + 图像位姿）
2. 查询图像提取特征 → 与3D点匹配 → PnP位姿估计
3. 根据位姿重新投影 → 生成投影图 + 坐标映射
4. 前端对比原图与投影图，标注匹配点连线
5. 多轮迭代优化位姿
6. 多种特征/匹配方案对比（SIFT / SuperPoint / LoFTR）

优化亮点（v2）：
- KD-Tree 空间索引加速点云筛选，O(N) → O(logN)
- 向量化像素搜索（cdist），嵌套 for 循环 → 矩阵运算
- PnP 结果全局缓存，差异化 matcher 共享同一套 PnP
- Tile-based 图像检索：利用已有投影图做 SIFT FLANN 匹配筛选 top-K 位姿候选
  （替代暴力遍历 50 个位姿，大幅减少 PnP 迭代次数）
"""
import json
import logging
import os
import time
from pathlib import Path
from collections import defaultdict

import cv2
import numpy as np
import torch
import kornia.feature as KF
from kornia.utils import image_to_tensor
from scipy.spatial import cKDTree
from scipy.spatial.distance import cdist

from services.las_processor.projection import _load_poses_and_offset, _quat_to_rotmat

# ── 设备选择 ──
try:
    if torch.backends.mps.is_available():
        DEVICE = torch.device("mps")
        print(f"[LOCALIZER] 使用 MPS (Metal GPU)")
    else:
        DEVICE = torch.device("cpu")
        print(f"[LOCALIZER] 使用 CPU")
except:
    DEVICE = torch.device("cpu")
from PIL import Image, ImageDraw, ImageFont

from services.las_processor.colmap_reader import read_images_txt, read_points3d_txt

# ── 深度学习特征匹配（懒加载） ──────────────
_DL_MATCHER = None  # LightGlueMatcher
_LOFTR = None       # LoFTR

LOG_DIR = Path(__file__).parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

_logger = logging.getLogger("localizer")
_logger.setLevel(logging.DEBUG)
_fh = logging.FileHandler(str(LOG_DIR / "localizer.log"), mode="a", encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
_logger.handlers.clear()
_logger.addHandler(_fh)
_sh = logging.StreamHandler()
_sh.setFormatter(logging.Formatter("%(asctime)s [LOCALIZER] %(message)s", datefmt="%H:%M:%S"))
_logger.addHandler(_sh)


def log(msg: str):
    _logger.info(msg)


# ── 全局缓存 ─────────────────────────────────────────
_COLMAP_POINTS = None
_COLMAP_IMAGES = None
_POINT_INDEX = None  # 3D点空间索引
_POSE_TREE = None    # 位姿空间 KD-Tree
_POSE_ARRAY = None   # 位姿坐标数组 (N, 3)

# PnP 结果缓存（按查询图像路径 → 位姿）
_PNP_CACHE: dict[str, dict] = {}

# Tile 检索索引（懒加载）
_TILE_INDEX: list[dict] | None = None  # tile_index.json
_TILE_SIFT_CACHE: dict[str, tuple] | None = None  # tile_name → (kp, des)
_TILE_FEATURES: dict[str, dict] | None = None  # tile_features_index.json
_RETRIEVAL_BUILT: bool = False


def load_colmap(las_dir: str = "las", force_reload: bool = False):
    """加载 COLMAP 数据（从 LAS 点云 + images.txt）"""
    global _COLMAP_POINTS, _COLMAP_IMAGES, _POINT_INDEX
    global _POSE_TREE, _POSE_ARRAY

    if _COLMAP_POINTS is not None and not force_reload:
        return _COLMAP_POINTS, _COLMAP_IMAGES

    log("加载 COLMAP 数据...")
    t0 = time.time()

    # 加载 images.txt（全景位姿）
    img_path = os.path.join(las_dir, "images.txt")
    _COLMAP_IMAGES = read_images_txt(img_path)
    log(f"  {len(_COLMAP_IMAGES)} 张图像, 耗时{time.time()-t0:.1f}s")

    # 从 LAS 点云构建 3D 点（采样）
    log("从点云构建3D点索引...")
    las_path = os.path.join(las_dir, "default_2026-05-28-112428.las")
    from laspy import open as las_open
    reader = las_open(las_path)
    pts = reader.read()

    # 采样500万点
    step = max(1, len(pts.x) // 5_000_000)
    n_pts = len(pts.x) // step

    # 构建简易 ColmapPoint3D 数据结构
    class _SimplePoint3D:
        def __init__(self, pid, x, y, z, r=128, g=128, b=128):
            self.point_id = pid
            self.x, self.y, self.z = x, y, z
            self.r, self.g, self.b = r, g, b

    _COLMAP_POINTS = {}
    xs_raw = np.array(pts.x[::step], dtype=np.float64)
    ys_raw = np.array(pts.y[::step], dtype=np.float64)
    zs_raw = np.array(pts.z[::step], dtype=np.float64)

    # 检测并转为局部坐标（与位姿坐标系一致）
    offset_x_l, offset_y_l, offset_z_l = 0.0, 0.0, 0.0
    if xs_raw.min() > 100000:
        map_cfg = os.path.join(las_dir, "map_config.json")
        if os.path.exists(map_cfg):
            with open(map_cfg) as _f:
                _cfg = json.load(_f)
            offset_x_l, offset_y_l, offset_z_l = _cfg["offset_xyz"]
    xs = xs_raw - offset_x_l
    ys = ys_raw - offset_y_l
    zs = zs_raw - offset_z_l

    has_rgb = hasattr(pts, 'red') and hasattr(pts, 'green') and hasattr(pts, 'blue')
    rs = (np.array(pts.red[::step], dtype=np.uint32) >> 8).astype(np.uint8) if has_rgb else None
    gs = (np.array(pts.green[::step], dtype=np.uint32) >> 8).astype(np.uint8) if has_rgb else None
    bs = (np.array(pts.blue[::step], dtype=np.uint32) >> 8).astype(np.uint8) if has_rgb else None

    for i in range(len(xs)):
        pid = i + 1
        r = int(rs[i]) if rs is not None else 128
        g = int(gs[i]) if gs is not None else 128
        b = int(bs[i]) if bs is not None else 128
        _COLMAP_POINTS[pid] = _SimplePoint3D(pid, float(xs[i]), float(ys[i]), float(zs[i]), r, g, b)

    log(f"  从点云构建 {len(_COLMAP_POINTS)} 个3D点, 耗时{time.time()-t0:.1f}s")

    # ── 构建 KD-Tree 空间索引 ──
    log("构建3D点 KD-Tree 空间索引...")
    pts_array = []
    colors_array = []
    pt_ids = []
    for pid, p in _COLMAP_POINTS.items():
        pts_array.append([p.x, p.y, p.z])
        colors_array.append([p.r, p.g, p.b])
        pt_ids.append(pid)
    _POINT_INDEX = {
        "pts": np.array(pts_array, dtype=np.float32),
        "colors": np.array(colors_array, dtype=np.uint8),
        "ids": np.array(pt_ids),
        "tree": cKDTree(np.array(pts_array, dtype=np.float64)),
    }
    log(f"  索引: {len(pts_array)} 个点")

    # ── 构建位姿 KD-Tree ──
    log("构建位姿 KD-Tree...")
    poses, _, _, _ = _load_poses_and_offset(las_dir)
    pose_xyz = np.array([[p['x'], p['y'], p['z']] for p in poses], dtype=np.float64)
    _POSE_ARRAY = pose_xyz
    _POSE_TREE = cKDTree(pose_xyz)
    log(f"  位姿: {len(poses)} 个")

    return _COLMAP_POINTS, _COLMAP_IMAGES


def get_point_cloud_arrays():
    """返回进程级缓存的点云数组，避免每次定位重建 500 万个 Python 对象。"""
    load_colmap()
    return _POINT_INDEX["pts"], _POINT_INDEX["colors"]


def _build_3d_point_descriptors(
    query_kp, query_des,
    known_images, known_points,
    top_k_images: int = 50,
):
    """
    通过已知图像位姿（images.txt），将查询图像与已知位姿的图像做特征匹配，
    然后利用已知位姿将2D点三角化为3D点。
    
    由于没有 COLMAP 图像文件，改用基于投影图的多 tile 匹配方案。
    """
    return np.array([]), np.array([])


def _solve_pnp(object_pts, image_pts, camera_matrix, dist_coeffs=None):
    """PnP 位姿估计"""
    if len(object_pts) < 4:
        return None, None, None

    if dist_coeffs is None:
        dist_coeffs = np.zeros((4, 1))

    success, rvec, tvec, inliers = cv2.solvePnPRansac(
        object_pts, image_pts, camera_matrix, dist_coeffs,
        iterationsCount=2000, reprojectionError=8.0, confidence=0.99,
        flags=cv2.SOLVEPNP_EPNP,
    )

    if not success:
        return None, None, None

    return rvec, tvec, inliers


def _rotation_matrix_to_quaternion(R):
    """旋转矩阵 → 四元数"""
    q = np.zeros(4)
    q[0] = np.sqrt(1 + R[0,0] + R[1,1] + R[2,2]) / 2
    q[1] = (R[2,1] - R[1,2]) / (4 * q[0])
    q[2] = (R[0,2] - R[2,0]) / (4 * q[0])
    q[3] = (R[1,0] - R[0,1]) / (4 * q[0])
    return q


def _get_camera_matrix(img_w, img_h, fov_deg=60):
    """根据图像尺寸和视场角估算内参矩阵"""
    f = max(img_w, img_h) / (2 * np.tan(np.deg2rad(fov_deg / 2)))
    return np.array([
        [f, 0, img_w / 2],
        [0, f, img_h / 2],
        [0, 0, 1]
    ], dtype=np.float64)


def reproject_points(rvec, tvec, camera_matrix, points_3d, img_w, img_h):
    """将3D点根据位姿和相机内参投影到2D图像平面"""
    if len(points_3d) == 0:
        return [], []

    rmat, _ = cv2.Rodrigues(rvec)
    # 投影
    projected, _ = cv2.projectPoints(points_3d, rvec, tvec, camera_matrix, None)
    projected = projected.reshape(-1, 2)

    # 筛选在图像范围内的点
    valid = (projected[:, 0] >= 0) & (projected[:, 0] < img_w) & \
            (projected[:, 1] >= 0) & (projected[:, 1] < img_h)
    
    return projected[valid], valid


def _estimate_surface_normals(points_3d, k=8):
    """基于局部邻域估计法线，用于近似表面着色。"""
    if points_3d is None or len(points_3d) < 3:
        return np.zeros((0, 3), dtype=np.float64)

    points = np.asarray(points_3d, dtype=np.float64)
    if len(points) == 3:
        return np.array([[0.0, 0.0, 1.0]] * 3, dtype=np.float64)

    # 使用最近邻简单近似：取点云中心和局部差分向量
    centroid = points.mean(axis=0)
    centered = points - centroid
    _, _, vh = np.linalg.svd(centered, full_matrices=False)
    normals = vh[-1].reshape(1, 3)
    normals = np.repeat(normals, len(points), axis=0)
    normals = normals / (np.linalg.norm(normals, axis=1, keepdims=True) + 1e-8)
    return normals


def _render_point_cloud_splat(points_3d, point_colors, camera_matrix, img_w, img_h, rvec=None, tvec=None, radius=1.5):
    """带软点扩散、法线光照和深度缓冲的相机式点云渲染。

    与 projection.py 的 _render_camera_like_points 保持一致的渲染质量。
    """
    if points_3d is None or len(points_3d) == 0:
        return np.zeros((img_h, img_w, 3), dtype=np.uint8)

    if rvec is None or tvec is None:
        return np.zeros((img_h, img_w, 3), dtype=np.uint8)

    rmat, _ = cv2.Rodrigues(rvec)
    points = np.asarray(points_3d, dtype=np.float64)
    colors = np.asarray(point_colors, dtype=np.float32)
    tvec_f = np.asarray(tvec, dtype=np.float64).reshape(1, 3)

    # 世界坐标 → 相机坐标
    camera_pts = points @ rmat.T + tvec_f
    z = camera_pts[:, 2]
    valid = np.isfinite(z) & (z > 1e-3)
    if not np.any(valid):
        return np.zeros((img_h, img_w, 3), dtype=np.uint8)

    camera_pts, z, colors = camera_pts[valid], z[valid], colors[valid]

    # 投影到图像平面
    px = np.rint(camera_matrix[0, 0] * camera_pts[:, 0] / z + camera_matrix[0, 2]).astype(np.int32)
    py = np.rint(camera_matrix[1, 1] * camera_pts[:, 1] / z + camera_matrix[1, 2]).astype(np.int32)

    inside = (px >= 0) & (px < img_w) & (py >= 0) & (py < img_h)
    if not np.any(inside):
        return np.zeros((img_h, img_w, 3), dtype=np.uint8)

    px, py, z, colors = px[inside], py[inside], z[inside], colors[inside]

    # 法线估计（用SVD平面拟合）
    centroid = camera_pts[inside].mean(axis=0)
    centered = camera_pts[inside] - centroid
    _, _, vh = np.linalg.svd(centered, full_matrices=False)
    normal = vh[-1]
    normal = normal / (np.linalg.norm(normal) + 1e-8)

    # 深度缓冲 + 软点扩散
    img = np.zeros((img_h, img_w, 3), dtype=np.float32)
    depth_map = np.full((img_h, img_w), np.inf, dtype=np.float32)
    light_dir = np.array([0.2, -0.2, 1.0], dtype=np.float64)
    light_dir = light_dir / (np.linalg.norm(light_dir) + 1e-8)
    shading_base = 0.35 + 0.65 * np.clip(np.dot(normal, light_dir), 0.0, 1.0)

    int_radius = int(np.ceil(radius))

    for idx in range(len(px)):
        xi, yi = px[idx], py[idx]
        zi = float(z[idx])
        if zi >= depth_map[yi, xi]:
            continue

        # RGB → BGR + 颜色uint16缩放到uint8
        color = np.array([float(colors[idx, 2]), float(colors[idx, 1]), float(colors[idx, 0])], dtype=np.float32)
        if color.max() > 255:
            color /= 256.0
        color = np.clip(color, 0, 255)

        view_factor = np.clip(1.0 - (zi / max(zi, 1.0)), 0.6, 1.0)
        base_val = color * view_factor * (0.7 + 0.3 * shading_base)

        for oy in range(-int_radius, int_radius + 1):
            for ox in range(-int_radius, int_radius + 1):
                nx, ny = xi + ox, yi + oy
                if 0 <= nx < img_w and 0 <= ny < img_h:
                    dist = np.sqrt(ox * ox + oy * oy)
                    if dist <= radius:
                        weight = max(0.0, 1.0 - dist / radius)
                        if zi < depth_map[ny, nx]:
                            img[ny, nx] = np.maximum(img[ny, nx], base_val * weight)
                            depth_map[ny, nx] = zi

    img = np.clip(img, 0, 255).astype(np.uint8)
    img = cv2.GaussianBlur(img, (3, 3), 0)
    # 画面偏暗，需要加大提亮（view_factor和shading把颜色压到了50-70%）
    img = cv2.convertScaleAbs(img, alpha=1.1, beta=15)
    return img


def _filter_points_by_distance(points_3d, point_colors, tvec, max_dist=150.0):
    """只保留位姿附近 max_dist 米内的3D点（避免远处稀疏点影响渲染质量）。"""
    t = np.asarray(tvec, dtype=np.float64).flatten()
    pts = np.asarray(points_3d, dtype=np.float64)
    dists = np.linalg.norm(pts - t, axis=1)
    mask = dists < max_dist
    if mask.sum() < 100:
        return points_3d, point_colors  # fallback: 使用全部
    return pts[mask], np.asarray(point_colors)[mask]


def render_projection_image(
    points_3d, point_colors,
    rvec, tvec, camera_matrix,
    img_w, img_h, output_path,
    resolution_scale=1.0,
    include_coord_map=False,
):
    """
    根据位姿将3D点云重新投影为2D图像。
    优先使用 octree_render (C++引擎) 获得高质量渲染，fallback 到 Python 渲染。

    返回: (输出图像路径, 像素到3D坐标映射)
    """
    w = int(img_w * resolution_scale)
    h = int(img_h * resolution_scale)

    tvec_local = np.array(tvec).flatten()[:3]

    # ── 尝试 octree_render（高质量）──
    try:
        from services.las_processor.projection_octree import (
            render_pose_octree, _depth_to_xyz_map, OCTREE_CONFIG
        )
        from services.las_processor.projection import _apply_camera_like_shading
        from PIL import Image
        import tempfile, os

        octree_dataset = 'projections/octree_data'
        if os.path.exists(os.path.join(octree_dataset, 'manifest.json')):
            # rvec → 旋转矩阵 → 四元数
            R, _ = cv2.Rodrigues(rvec)
            q = _rotation_matrix_to_quaternion(R)
            qw, qx, qy, qz = float(q[0]), float(q[1]), float(q[2]), float(q[3])
            tx, ty, tz = float(tvec_local[0]), float(tvec_local[1]), float(tvec_local[2])
            colmap_line = f"{qw:.10f} {qx:.10f} {qy:.10f} {qz:.10f} {tx:.6f} {ty:.6f} {tz:.6f}"

            fov_deg = 75
            f = max(w, h) / (2 * np.tan(np.deg2rad(fov_deg / 2)))
            focal_norm = f / max(w, h)

            with tempfile.TemporaryDirectory(prefix='pnp_render_') as tmpdir:
                color_ppm = os.path.join(tmpdir, 'color.ppm')
                depth_raw = os.path.join(tmpdir, 'depth.raw')
                ok = render_pose_octree(octree_dataset, colmap_line, w, h, focal_norm, color_ppm, depth_raw)

                if ok and os.path.exists(color_ppm):
                    with Image.open(color_ppm) as img:
                        color_img = np.array(img.convert('RGB'))
                    if os.path.exists(depth_raw):
                        depth = np.fromfile(depth_raw, dtype=np.float32)
                        if depth.size == w * h:
                            depth = depth.reshape(h, w)
                            color_img = _apply_camera_like_shading(color_img, depth=depth)
                    else:
                        color_img = _apply_camera_like_shading(color_img)

                    cv2.imwrite(output_path, cv2.cvtColor(color_img, cv2.COLOR_RGB2BGR))
                    log(f"  [RENDER] octree_render 成功")

                    coord_map = {}
                    if include_coord_map and os.path.exists(depth_raw):
                        depth = np.fromfile(depth_raw, dtype=np.float32)
                        if depth.size == w * h:
                            depth = depth.reshape(h, w)
                            fx = fy = f
                            cx_ = (w - 1) / 2.0
                            cy_ = (h - 1) / 2.0
                            _, world_array = _depth_to_xyz_map(
                                depth, fx, fy, cx_, cy_,
                                qw, qx, qy, qz, tx, ty, tz, (0, 0, 0)
                            )
                            valid_mask = np.any(world_array != 0, axis=2)
                            for py_i in range(h):
                                for px_i in range(w):
                                    if valid_mask[py_i, px_i]:
                                        coord_map[f"{px_i},{py_i}"] = [
                                            float(world_array[py_i, px_i, 0]),
                                            float(world_array[py_i, px_i, 1]),
                                            float(world_array[py_i, px_i, 2]),
                                        ]
                    return output_path, coord_map
    except Exception as e:
        log(f"  [RENDER] octree_render 异常: {e}，fallback")

    # ── Fallback: Python 渲染 ──
    points_3d, point_colors = _filter_points_by_distance(points_3d, point_colors, tvec, max_dist=150.0)
    K = camera_matrix.copy()
    K[:2] *= resolution_scale

    img = _render_point_cloud_splat(points_3d, point_colors, K, w, h, rvec=rvec, tvec=tvec_local, radius=1.2)
    if img.size == 0:
        return None, {}

    coord_map = {}
    if include_coord_map:
        projected, valid = reproject_points(rvec, tvec_local, K, points_3d, w, h)
        valid_3d = points_3d[valid]
        coord_map = {
            f"{int(round(p[0]))},{int(round(p[1]))}": [float(v) for v in xyz]
            for p, xyz in zip(projected, valid_3d)
        }

    cv2.imwrite(output_path, img)
    return output_path, coord_map


def _extract_features(image, method="sift"):
    """按指定方法提取特征"""
    if isinstance(image, str):
        image = cv2.imread(image)
        if image is None:
            return [], None
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image

    if method == "sift":
        detector = cv2.SIFT_create(nfeatures=3000)
        return detector.detectAndCompute(gray, None)
    elif method == "orb":
        detector = cv2.ORB_create(nfeatures=3000, scaleFactor=1.2, nlevels=8)
        kp, des = detector.detectAndCompute(gray, None)
        if des is not None:
            des = des.astype(np.uint8)
        return kp, des
    elif method == "akaze":
        detector = cv2.AKAZE_create()
        return detector.detectAndCompute(gray, None)
    elif method in ("lightglue", "superpoint", "loftr"):
        # 深度学习方法：返回原始图像用于后续匹配
        kp = [cv2.KeyPoint(0, 0, 1)]  # 占位
        return kp, gray  # 返回灰度图代替descriptor
    else:
        detector = cv2.SIFT_create(nfeatures=3000)
        return detector.detectAndCompute(gray, None)


def _init_dl_matcher():
    """初始化深度学习匹配器（懒加载）"""
    global _DL_MATCHER, _LOFTR
    if _DL_MATCHER is None:
        log(f"加载 LightGlue 模型 ({DEVICE})...")
        _DL_MATCHER = KF.LightGlueMatcher('sift').to(DEVICE)
    if _LOFTR is None:
        log(f"加载 LoFTR 模型 ({DEVICE})...")
        _LOFTR = KF.LoFTR(pretrained='outdoor').to(DEVICE)


def _match_features(des1, des2, method="flann", ratio=0.75, img1=None, img2=None):
    """按指定方法匹配特征

    方法:
      flann        - FLANN kd-tree + Lowe 0.75
      bf           - BruteForce + Lowe 0.75
      flann_lowes  - FLANN + 严格 Lowe 0.6
      bf_cross     - BruteForce + 交叉验证
      knn_rank     - FLANN + top-50
      lightglue    - LightGlue (深度学习)
      loftr        - LoFTR (深度学习)
    """
    # 深度学习方法
    if method in ("lightglue", "loftr"):
        return _match_deep(img1, img2, method)

    # 传统方法
    if des1 is None or des2 is None or len(des1) < 2 or len(des2) < 2:
        return []

    is_binary = des1.dtype == np.uint8

    if method == "flann":
        params = (dict(algorithm=1, trees=5), dict(checks=50))
        matcher = cv2.FlannBasedMatcher(*params)
        use_knn, test_ratio = True, 0.75
    elif method == "bf":
        norm = cv2.NORM_HAMMING if is_binary else cv2.NORM_L2
        matcher = cv2.BFMatcher(norm, crossCheck=False)
        use_knn, test_ratio = True, 0.75
    elif method == "flann_lowes":
        params = (dict(algorithm=1, trees=5), dict(checks=50))
        matcher = cv2.FlannBasedMatcher(*params)
        use_knn, test_ratio = True, 0.6
    elif method == "bf_cross":
        norm = cv2.NORM_HAMMING if is_binary else cv2.NORM_L2
        bf12 = cv2.BFMatcher(norm, crossCheck=False)
        bf21 = cv2.BFMatcher(norm, crossCheck=False)
        try:
            m12 = bf12.knnMatch(des1, des2, k=2)
            m21 = bf21.knnMatch(des2, des1, k=2)
        except cv2.error:
            return []
        good = []
        for pair in m12:
            if len(pair) == 2 and pair[0].distance < 0.75 * pair[1].distance:
                for pair2 in m21:
                    if len(pair2) == 2 and pair2[0].trainIdx == pair[0].queryIdx:
                        if pair2[0].distance < 0.75 * pair2[1].distance:
                            good.append(pair[0])
                            break
        return good
    elif method == "knn_rank":
        params = (dict(algorithm=1, trees=5), dict(checks=50))
        matcher = cv2.FlannBasedMatcher(*params)
        try:
            matches = matcher.knnMatch(des1, des2, k=2)
        except cv2.error:
            return []
        all_m = [m for pair in matches if len(pair) > 0 for m in [pair[0]]]
        all_m.sort(key=lambda x: x.distance)
        return all_m[:min(50, len(all_m))]
    else:
        params = (dict(algorithm=1, trees=5), dict(checks=50))
        matcher = cv2.FlannBasedMatcher(*params)
        use_knn, test_ratio = True, 0.75

    # 通用 knnMatch + Lowe 比率测试
    try:
        knn = matcher.knnMatch(des1, des2, k=2)
    except cv2.error:
        return []

    good = []
    for pair in knn:
        if len(pair) == 2:
            m, n = pair[0], pair[1]
            if m.distance < test_ratio * n.distance:
                good.append(m)
    return good


def _match_deep(img1_gray, img2_gray, method="lightglue", img1_full=None, img2_full=None):
    """深度学习特征匹配（LightGlue / LoFTR）"""
    _init_dl_matcher()
    
    # 确保输入是灰度图
    if len(img1_gray.shape) == 3:
        img1_gray = cv2.cvtColor(img1_gray, cv2.COLOR_BGR2GRAY)
    if len(img2_gray.shape) == 3:
        img2_gray = cv2.cvtColor(img2_gray, cv2.COLOR_BGR2GRAY)
    
    # 转为 tensor
    h1, w1 = img1_gray.shape
    h2, w2 = img2_gray.shape
    
    if method == "lightglue":
        # LightGlue 需要 SIFT 特征 + 深度学习匹配
        sift = cv2.SIFT_create(nfeatures=2000)
        kp1, des1 = sift.detectAndCompute(img1_gray, None)
        kp2, des2 = sift.detectAndCompute(img2_gray, None)
        if des1 is None or des2 is None or len(des1) < 5 or len(des2) < 5:
            return []
        
        # 转为 tensor (移到 GPU)
        desc1 = torch.from_numpy(des1.astype(np.float32)).unsqueeze(0).to(DEVICE)
        desc2 = torch.from_numpy(des2.astype(np.float32)).unsqueeze(0).to(DEVICE)
        kpts1 = torch.from_numpy(np.array([k.pt for k in kp1], dtype=np.float32)).unsqueeze(0).to(DEVICE)
        kpts2 = torch.from_numpy(np.array([k.pt for k in kp2], dtype=np.float32)).unsqueeze(0).to(DEVICE)
        
        with torch.no_grad():
            result = _DL_MATCHER(desc1, desc2, kpts1, kpts2)
        
        # 解析结果
        matches = []
        if result is not None and len(result) >= 3:
            match_indices = result[0]  # (N, 2) tensor
            for idx in range(match_indices.shape[0]):
                i, j = int(match_indices[idx, 0]), int(match_indices[idx, 1])
                # 创建 cv2.DMatch 兼容对象
                class _DMatch:
                    def __init__(self, qi, ti, d):
                        self.queryIdx = qi
                        self.trainIdx = ti
                        self.distance = d
                matches.append(_DMatch(i, j, 0.0))
        return matches
        
    elif method == "loftr":
        # LoFTR: 端到端匹配（不需要特征点）
        img1_tensor = image_to_tensor(img1_gray, keepdim=True).unsqueeze(0).float().to(DEVICE) / 255.0
        img2_tensor = image_to_tensor(img2_gray, keepdim=True).unsqueeze(0).float().to(DEVICE) / 255.0
        
        with torch.no_grad():
            corr = _LOFTR({
                'image0': img1_tensor,
                'image1': img2_tensor,
            })
        
        class _LoftrMatches(list):
            def __init__(self):
                super().__init__()
                self.kpts0 = None
                self.kpts1 = None
        
        matches = _LoftrMatches()
        if 'keypoints0' in corr and 'keypoints1' in corr:
            kpts0_all = corr['keypoints0']
            kpts1_all = corr['keypoints1']
            if len(kpts0_all) > 0 and len(kpts1_all) > 0:
                kpts0 = kpts0_all[0].cpu().numpy()
                kpts1 = kpts1_all[0].cpu().numpy()
                matches.kpts0 = kpts0
                matches.kpts1 = kpts1
                
                for i in range(min(len(kpts0), len(kpts1))):
                    class _DMatch:
                        def __init__(self, qi, ti, d):
                            self.queryIdx = qi
                            self.trainIdx = ti
                            self.distance = d
                    matches.append(_DMatch(i, i, 0.0))
        
        return matches
    
    return []


def _find_nearby_keypoints_vectorized(px, py, q_kp, search_radius, pts_3d):
    """
    向量化版：使用 cdist 一次性找到投影点附近的关键点。
    
    返回: (local_3d, local_2d, matched_indices)
    """
    if len(px) == 0 or len(q_kp) == 0:
        return [], [], []
    
    # 构建投影点坐标 (N, 2)
    proj_pts = np.column_stack([px, py])
    
    # 构建关键点坐标 (M, 2)
    kp_pts = np.array([(kp.pt[0], kp.pt[1]) for kp in q_kp], dtype=np.float32)
    
    # 计算距离矩阵 (N, M)，使用 L1 距离（曼哈顿）以匹配原来的 abs 比较
    dists = cdist(proj_pts, kp_pts, metric='cityblock')
    
    # 找到每个投影点最近的关键点
    min_dists = dists.min(axis=1)
    min_indices = dists.argmin(axis=1)
    
    # 筛选在搜索半径内的匹配
    valid_mask = min_dists < search_radius
    
    if not valid_mask.any():
        return [], [], []
    
    valid_proj_idx = np.where(valid_mask)[0]
    
    # 去重：避免多个投影点匹配到同一个关键点
    used_kp = set()
    local_3d, local_2d = [], []
    matched_proj_indices = []
    
    for idx in valid_proj_idx:
        kp_idx = min_indices[idx]
        if kp_idx in used_kp:
            continue
        used_kp.add(kp_idx)
        local_3d.append(pts_3d[idx].tolist())
        local_2d.append([float(kp_pts[kp_idx, 0]), float(kp_pts[kp_idx, 1])])
        matched_proj_indices.append(idx)
    
    return local_3d, local_2d, matched_proj_indices


def _compute_pose_projection(pose, pts_all, camera_matrix, q_w, q_h, half_range=50.0):
    """
    对一个位姿计算点云投影，返回投影坐标和对应的3D点。
    
    使用 KD-Tree 快速筛选附近点。
    返回: (px, py, pts_3d_near) 或在无效时返回 None
    """
    pose_center = np.array([[pose['x'], pose['y'], pose['z']]], dtype=np.float64)
    
    # KD-Tree: 球面范围查询（half_range 半径）
    tree = _POINT_INDEX["tree"]
    near_indices = tree.query_ball_point(pose_center[0], r=half_range)
    
    if len(near_indices) < 50:
        return None
    
    pts_near = _POINT_INDEX["pts"][near_indices]
    
    # 计算旋转矩阵
    R_cw = _quat_to_rotmat(pose['qx'], pose['qy'], pose['qz'], pose['qw'])
    t_cw = np.array([pose['x'], pose['y'], pose['z']], dtype=np.float64)
    
    # 世界坐标 → 相机坐标
    pts_cam = (pts_near - t_cw) @ R_cw.T  # Nx3
    
    # 投影到图像平面
    valid = pts_cam[:, 2] > 0.1  # 相机前方
    if valid.sum() < 30:
        return None
    
    pts_proj = pts_cam[valid]
    px = pts_proj[:, 0] / pts_proj[:, 2] * camera_matrix[0,0] + camera_matrix[0,2]
    py = pts_proj[:, 1] / pts_proj[:, 2] * camera_matrix[1,1] + camera_matrix[1,2]
    
    # 筛选在图像范围内的点
    in_img = (px >= 0) & (px < q_w) & (py >= 0) & (py < q_h)
    if in_img.sum() < 10:
        return None
    
    pts_3d_valid = pts_near[valid][in_img]
    
    return px[in_img].astype(np.int32), py[in_img].astype(np.int32), pts_3d_valid


def localize_image(
    query_image_path: str,
    output_dir: str = "projections/localize",
    feature_method: str = "sift",
    match_method: str = "flann",
    max_iterations: int = 3,
    debug_visualizations: bool = False,
) -> dict:
    """
    端到端视觉定位主函数（v2 优化版）。

    使用 panoramicPoses 位姿直接做 PnP 初始化 + 迭代优化：
    1. 加载 panoramicPoses 位姿（局部坐标）
    2. 对每个位姿，将点云投影到图像平面
    3. 在投影位置附近搜索查询图像的特征点（向量化 cdist）
    4. 收集 3D-2D 匹配点对做 PnP
    5. 迭代优化位姿
    
    优化：
    - KD-Tree 空间索引加速点云筛选
    - cdist 向量化像素搜索
    - PnP 结果缓存（相同图像不同 matcher 共享）
    """
    # ── SALAD 系列算法 ──
    if match_method in ("salad_roma", "salad_lightglue", "ace"):
        from services.localizer.salad_roma import localize_with_salad_roma
        algo = {"salad_roma": "roma", "salad_lightglue": "lightglue", "ace": "ace"}.get(match_method, "lightglue")
        return localize_with_salad_roma(
            query_image_path,
            output_dir=output_dir,
            max_iterations=max_iterations,
            top_k_retrieval=3,
            debug_visualizations=debug_visualizations,
            algo=algo,
        )

    tag = f"{feature_method}_{match_method}"
    log(f"{'='*60}")
    log(f"🚀 视觉定位 [{tag}]: {os.path.basename(query_image_path)}")

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # ── PnP 缓存检查 ──
    cache_key = f"{os.path.abspath(query_image_path)}"
    if cache_key in _PNP_CACHE:
        # 删除缓存，强制重新定位（用户修改定位参数后需要重新跑）
        del _PNP_CACHE[cache_key]
        log(f"🔄 清除旧缓存，强制重新定位")

    # ── 首次运行：完整 PnP 流程 ──
    # 1. 读取查询图像
    query_img = cv2.imread(query_image_path)
    if query_img is None:
        return {"success": False, "error": "Cannot read query image", "tag": tag}
    q_h_orig, q_w_orig = query_img.shape[:2]
    # 统一缩放到 512x512 正方形，与批量投影(TILE_PX=512)和slam-map一致
    # PnP计算、重投影渲染统一用这个尺寸
    q_small = cv2.resize(query_img, (512, 512))
    q_h, q_w = 512, 512
    scale_x = 512 / q_w_orig
    scale_y = 512 / q_h_orig
    
    camera_matrix = _get_camera_matrix(q_w_orig, q_h_orig, fov_deg=75)
    camera_matrix[0,0] *= scale_x
    camera_matrix[1,1] *= scale_y
    camera_matrix[0,2] = 256.0
    camera_matrix[1,2] = 256.0
    log(f"📷 {q_w_orig}x{q_h_orig} → {q_w}x{q_h} (512x512)")

    # 2. 提取查询图像 SIFT 特征
    q_gray = cv2.cvtColor(q_small, cv2.COLOR_BGR2GRAY)
    q_kp, q_des = cv2.SIFT_create(nfeatures=3000).detectAndCompute(q_gray, None)
    if q_kp is None:
        return {"success": False, "error": "No features", "tag": tag}
    log(f"🔍 {len(q_kp)} 特征点")

    # 3. 加载点云
    known_points, known_images = load_colmap()
    pts_all = _POINT_INDEX["pts"]
    log(f"🗺️ {len(pts_all)} 个3D点")

    # 4. 加载位姿
    poses, ox, oy, oz = _load_poses_and_offset()
    log(f"📐 {len(poses)} 个位姿")

    # ── 5. Tile 检索：用 SIFT + FLANN 匹配投影图，筛选 top-K 候选位姿 ──
    t0_pose = time.time()
    
    best_inliers = 0
    best_rvec, best_tvec = None, None
    best_3d, best_2d = None, None

    half_range = 50.0
    search_radius = 15  # 像素搜索半径
    
    # Step A: 用 SIFT 匹配已有投影图做图像检索
    tile_results = _retrieve_top_poses_by_sift(
        q_kp, q_des,
        top_k=5,           # 取 top-5 个最匹配的 tile
        match_ratio=0.75,
        min_matches=10,
    )
    
    if tile_results:
        log(f"  Tile检索命中 {len(tile_results)} 个候选")
        # 从 tile 匹配结果解析位姿
        candidate_poses = []
        seen_pose_idx = set()
        for n_matches, ti, view_type, tile_key in tile_results:
            pose = _resolve_pose_from_tile(tile_key)
            if pose is not None:
                # 用 (x, y, z) 去重（同一个位姿可能被多个视角匹配到）
                pk = (round(pose['x'], 1), round(pose['y'], 1))
                if pk not in seen_pose_idx:
                    seen_pose_idx.add(pk)
                    candidate_poses.append(pose)
                    log(f"    候选位姿: x={pose['x']:.1f} y={pose['y']:.1f} ({n_matches}匹, {view_type})")
    else:
        # Fallback: SIFT 检索未命中，用所有位姿
        log(f"  Tile检索无结果, fallback到全部位姿")
        candidate_poses = poses[:min(50, len(poses))]
    
    # 如果 tile 检索匹配数太少（<50 个总匹配点），扩展候选到 top-20 个位姿
    if tile_results and sum(r[0] for r in tile_results) < 50:
        log(f"  Tile检索匹配总数不足50, 扩展候选位姿")
        all_poses = sorted(poses, key=lambda p: p['z'], reverse=True)[:min(20, len(poses))]
        seen_xyz = set((round(p['x'],1), round(p['y'],1)) for p in candidate_poses)
        for p in all_poses:
            pk = (round(p['x'],1), round(p['y'],1))
            if pk not in seen_xyz:
                seen_xyz.add(pk)
                candidate_poses.append(p)
        log(f"  扩展后共 {len(candidate_poses)} 个候选位姿")
    
    # Step B: 只对候选位姿做 PnP 投影匹配
    pose_count = 0
    for pi, pose in enumerate(candidate_poses):
        result = _compute_pose_projection(pose, pts_all, camera_matrix, q_w, q_h, half_range)
        if result is None:
            continue
        
        px_in, py_in, pts_3d_valid = result
        
        # 向量化像素搜索
        local_3d, local_2d, _ = _find_nearby_keypoints_vectorized(
            px_in, py_in, q_kp, search_radius, pts_3d_valid
        )
        
        if len(local_3d) < 4:
            continue
        
        pose_count += 1
        
        # PnP
        rvec_i, tvec_i, inliers_i = _solve_pnp(
            np.array(local_3d, dtype=np.float64),
            np.array(local_2d, dtype=np.float64),
            camera_matrix
        )
        if rvec_i is not None:
            ic = len(inliers_i) if inliers_i is not None else len(local_3d)
            if ic > best_inliers:
                best_inliers = ic
                best_rvec, best_tvec = rvec_i, tvec_i
                best_3d, best_2d = local_3d, local_2d
                log(f"  pose#{pi}: {ic}内点 (最佳)")

    log(f"  位姿处理耗时: {time.time()-t0_pose:.1f}s, 共 {pose_count} 个有效位姿")

    if best_rvec is None:
        return {"success": False, "error": "PnP failed for all poses", "tag": tag}

    log(f"✅ PnP成功: 内点={best_inliers}")

    # 6. 迭代优化（向量化版），最大10轮
    MAX_ROUNDS = 10
    rvec, tvec = best_rvec, best_tvec
    inlier_count = best_inliers

    tree = _POINT_INDEX["tree"]
    half_range_iter = 100.0

    for iteration in range(1, MAX_ROUNDS + 1):
        log(f"🔄 迭代 {iteration}/{MAX_ROUNDS}...")
        
        tvec_flat = tvec.flatten()
        near_indices = tree.query_ball_point(tvec_flat, r=half_range_iter)
        
        if len(near_indices) == 0:
            continue
            
        pts_near_iter = pts_all[near_indices]
        
        reprojected, valid_mask = reproject_points(rvec, tvec, camera_matrix, pts_near_iter, q_w, q_h)
        if len(reprojected) == 0:
            continue
        
        px_r = reprojected[:, 0].astype(np.int32)
        py_r = reprojected[:, 1].astype(np.int32)
        pts_3d_iter = pts_near_iter[valid_mask]
        
        new_3d, new_2d, _ = _find_nearby_keypoints_vectorized(
            px_r, py_r, q_kp, search_radius, pts_3d_iter
        )
        
        if len(new_3d) >= 4:
            nr, nt, ni = _solve_pnp(np.array(new_3d), np.array(new_2d), camera_matrix)
            if nr is not None:
                nic = len(ni) if ni is not None else len(new_3d)
                if nic > inlier_count:
                    rvec, tvec = nr, nt
                    inlier_count = nic
                    log(f"  → {nic}内点")

    # ── 缓存 PnP 结果 ──
    _PNP_CACHE[cache_key] = {
        'rvec': rvec,
        'tvec': tvec,
        'inliers': inlier_count,
        'best_3d': best_3d,
        'best_2d': best_2d,
        'q_small': q_small,
        'q_w': q_w,
        'q_h': q_h,
        'camera_matrix': camera_matrix,
        'known_points': known_points,
        'q_kp': q_kp,
    }

    # 7. 生成重投影
    return _render_results(
        rvec, tvec, inlier_count, best_3d, best_2d,
        known_points, camera_matrix, q_w, q_h, q_small,
        out, tag
    )


# ── Tile 检索：利用投影图做图像级匹配筛选位姿 ──────────

def _load_tile_index():
    """懒加载 tile 索引"""
    global _TILE_INDEX, _TILE_FEATURES
    if _TILE_INDEX is None:
        idx_path = Path("projections/tile_index.json")
        if idx_path.exists():
            with open(idx_path) as f:
                _TILE_INDEX = json.load(f)
            log(f"  Tile索引: {len(_TILE_INDEX)} 个投影图")
        feat_path = Path("projections/tile_features_index.json")
        if feat_path.exists():
            with open(feat_path) as f:
                _TILE_FEATURES = json.load(f)
    return _TILE_INDEX


def _extract_tile_sift(name_key: str, tile_info: dict) -> tuple:
    """提取或缓存 tile 的 SIFT 特征"""
    global _TILE_SIFT_CACHE
    if _TILE_SIFT_CACHE is None:
        _TILE_SIFT_CACHE = {}
    if name_key in _TILE_SIFT_CACHE:
        return _TILE_SIFT_CACHE[name_key]
    
    img_path = tile_info.get("path", tile_info.get("image_path"))
    if not img_path or not Path(img_path).exists():
        return None, None
    
    img = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None, None
    
    sift = cv2.SIFT_create(nfeatures=1000)
    kp, des = sift.detectAndCompute(img, None)
    _TILE_SIFT_CACHE[name_key] = (kp, des)
    return kp, des


def _retrieve_top_poses_by_sift(
    q_kp, q_des,
    top_k: int = 5,
    match_ratio: float = 0.75,
    min_matches: int = 10,
) -> list[tuple[int, int, str, int]]:
    """
    用 SIFT + FLANN 匹配查询图像 vs 所有投影 tile，
    返回 top-K 匹配的 tile 列表。
    
    返回: [(匹配数, tile_index, view_type, tile_key), ...] 按匹配数降序
    """
    tile_index = _load_tile_index()
    if tile_index is None:
        return []
    
    t0 = time.time()
    
    # FLANN 匹配器
    flann = cv2.FlannBasedMatcher(
        dict(algorithm=1, trees=5), dict(checks=50)
    )
    
    results = []
    for ti, tile in enumerate(tile_index):
        # 跳过 top 视图（俯视图与查询图视角差异大，匹配效果差）
        view = tile.get("view", "front")
        if view == "top":
            continue
        
        name_key = os.path.splitext(os.path.basename(tile["image_path"]))[0]
        kp_tile, des_tile = _extract_tile_sift(name_key, tile)
        
        if des_tile is None or len(des_tile) < 10:
            continue
        
        try:
            knn = flann.knnMatch(q_des, des_tile, k=2)
        except cv2.error:
            continue
        
        good = 0
        for pair in knn:
            if len(pair) == 2:
                m, n = pair[0], pair[1]
                if m.distance < match_ratio * n.distance:
                    good += 1
        
        if good >= min_matches:
            results.append((good, ti, view, name_key))
    
    # 按匹配数降序排列
    results.sort(key=lambda x: -x[0])
    
    elapsed = time.time() - t0
    log(f"  Tile检索: {len(tile_index)} tiles, {elapsed:.1f}s, 找到 {len(results)} 匹配tiles")
    
    if results:
        top5_str = '; '.join(f'{r[2]}/{r[3].split("_")[-1]}({r[0]}匹)' for r in results[:5])
        log(f"  Top-5: {top5_str}")
    
    return results[:top_k]


def _resolve_pose_from_tile(tile_key: str) -> dict | None:
    """
    从 tile key (如 "view_top_-1.1_0.5_0") 解析出对应位姿。
    返回 pose dict (包含 x, y, z, qx, qy, qz, qw)。
    """
    # tile_key format: "view_{view}_{x}_{y}_{pose_idx}"
    # 例如 "view_top_-1.1_0.5_0" → 对应 panoramicPoses 中的第 0 个位姿
    parts = tile_key.split("_")
    # 最后一部分是 pose 索引
    try:
        pose_idx = int(parts[-1])
    except (ValueError, IndexError):
        return None
    
    poses, _, _, _ = _load_poses_and_offset()
    if 0 <= pose_idx < len(poses):
        return poses[pose_idx]
    
    # fallback: 通过坐标匹配
    # 坐标在 key 中: view_top_-1.1_0.5_0 → x=-1.1, y=0.5
    try:
        px = float(parts[2])
        py = float(parts[3])
    except (ValueError, IndexError):
        return None
    
    for pose in poses:
        if abs(pose['x'] - px) < 0.1 and abs(pose['y'] - py) < 0.1:
            return pose
    
    return None


def _render_results(
    rvec, tvec, inlier_count, best_3d, best_2d,
    known_points, camera_matrix, q_w, q_h, q_small,
    out: Path, tag: str,
) -> dict:
    """
    生成重投影图像和双图对比。
    
    对比图展示：
    - 左: 查询原图 (query)
    - 右: 重投影图 (reprojection)
    - 连线: 原图 SIFT 特征点 ↔ 重投影图对应位置 SIFT 特征点
            (通过 SIFT + FLANN 匹配得到，不是 PnP 内点)
    - 相似度: 基于匹配点数和平均距离的评分
    """
    
    all_pts = np.array([(p.x, p.y, p.z) for p in known_points.values()], dtype=np.float64)
    all_col = np.array([(p.r, p.g, p.b) for p in known_points.values()], dtype=np.uint8)

    proj_path = str(out / f"reprojection_{tag}.png")
    t0 = time.time()
    proj_path, coord_map = render_projection_image(all_pts, all_col, rvec, tvec, camera_matrix, q_w, q_h, proj_path)
    log(f"  重投影渲染耗时: {time.time()-t0:.1f}s")

    coord_path = str(out / f"reprojection_coord_{tag}.json")
    with open(coord_path, "w") as f:
        json.dump({"width": q_w, "height": q_h, "pixels": coord_map}, f)

    # ── 双图对比：SIFT 匹配原图 vs 重投影图 ──
    comparison_path = None
    matched_points_out = []
    similarity_score = 0.0
    
    if proj_path:
        proj_img = cv2.imread(proj_path)
        if proj_img is not None:
            # 提取原图和重投影图的 SIFT 特征
            q_gray = cv2.cvtColor(q_small, cv2.COLOR_BGR2GRAY)
            p_gray = cv2.cvtColor(proj_img, cv2.COLOR_BGR2GRAY) if len(proj_img.shape) == 3 else proj_img
            
            sift = cv2.SIFT_create(nfeatures=2000)
            kp1, des1 = sift.detectAndCompute(q_gray, None)
            kp2, des2 = sift.detectAndCompute(p_gray, None)
            
            if des1 is not None and des2 is not None and len(des1) > 5 and len(des2) > 5:
                # FLANN 匹配
                flann = cv2.FlannBasedMatcher(dict(algorithm=1, trees=5), dict(checks=50))
                knn = flann.knnMatch(des1, des2, k=2)
                
                good_matches = []
                for pair in knn:
                    if len(pair) == 2:
                        m, n = pair[0], pair[1]
                        if m.distance < 0.75 * n.distance:
                            good_matches.append(m)
                
                log(f"  原图vs重投影 SIFT 匹配: {len(good_matches)} 个好匹配")
                
                if good_matches:
                    # 排序取前 20 个最佳匹配用于展示
                    good_matches.sort(key=lambda x: x.distance)
                    display_matches = good_matches[:20]
                    
                    # 计算相似度: 匹配数 × (1 - 平均距离/500)
                    avg_dist = sum(m.distance for m in good_matches) / len(good_matches)
                    similarity_score = len(good_matches) * (1.0 - min(avg_dist / 500.0, 1.0))
                    
                    # 绘制对比图
                    h = max(q_h, proj_img.shape[0])
                    w = q_w + proj_img.shape[1]
                    canvas = np.zeros((h, w, 3), dtype=np.uint8)
                    canvas[:q_h, :q_w] = q_small
                    canvas[:proj_img.shape[0], q_w:] = proj_img
                    
                    colors = [
                        (255,0,0),(0,255,0),(0,0,255),(255,255,0),(255,0,255),
                        (0,255,255),(128,0,128),(255,128,0),(0,128,255),(128,255,0),
                        (255,0,128),(0,255,128),(128,0,255),(255,128,128),(128,255,128),
                        (128,128,255),(255,255,128),(255,128,255),(128,255,255),(200,200,200),
                    ]
                    for i, m in enumerate(display_matches):
                        x1, y1 = int(kp1[m.queryIdx].pt[0]), int(kp1[m.queryIdx].pt[1])
                        x2, y2 = int(kp2[m.trainIdx].pt[0]) + q_w, int(kp2[m.trainIdx].pt[1])
                        c = colors[i % len(colors)]
                        cv2.circle(canvas, (x1, y1), 5, c, -1)
                        cv2.circle(canvas, (x2, y2), 5, c, -1)
                        cv2.line(canvas, (x1, y1), (x2, y2), c, 1)
                        matched_points_out.append({
                            "x1": x1, "y1": y1,
                            "x2": x2 - q_w, "y2": y2,
                            "color": list(c),
                            "distance": float(m.distance),
                        })
                    
                    comparison_path = str(out / f"comparison_{tag}.png")
                    cv2.imwrite(comparison_path, canvas)
                    log(f"✅ 双图对比: {comparison_path} ({len(display_matches)}个匹配点, 相似度={similarity_score:.1f})")
                else:
                    # 无匹配时也生成一张纯对比图
                    log("  原图vs重投影无匹配, 生成纯对比图")
                    h = max(q_h, proj_img.shape[0])
                    w = q_w + proj_img.shape[1]
                    canvas = np.zeros((h, w, 3), dtype=np.uint8)
                    canvas[:q_h, :q_w] = q_small
                    canvas[:proj_img.shape[0], q_w:] = proj_img
                    comparison_path = str(out / f"comparison_{tag}.png")
                    cv2.imwrite(comparison_path, canvas)

    rmat, _ = cv2.Rodrigues(rvec)
    quat = _rotation_matrix_to_quaternion(rmat)

    result = {
        "success": True,
        "tag": tag,
        "feature_method": "pose_reprojection",
        "match_method": "pnp",
        "pose": {
            "quaternion": [float(q) for q in quat],
            "translation": tvec.flatten().tolist(),
        },
        "inliers": int(inlier_count),
        "total_3d_points": len(best_3d) if best_3d is not None else 0,
        "reprojection_image": proj_path,
        "comparison_image": comparison_path,
        "coord_map": coord_path,
        "matched_points": matched_points_out,
        "similarity_score": round(similarity_score, 1),
    }

    log(f"✅ [{tag}] 完成: 内点={inlier_count}")
    log(f"{'='*60}")
    return result
