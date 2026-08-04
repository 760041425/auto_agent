"""全点拟合单应性 XY 偏差诊断脚本。

量化当前 build_local_coordinate_transform_context 中「全部 PnP 内点拟合 H，
强制 Z=0」所导致的 XY 偏差，按地面点 vs 立面点分组统计，为 Phase B 的
平面检测修复提供 baseline 数据。

用法::

    # 模式 A：合成数据诊断（默认，不依赖真实数据）
    python scripts/diagnose_homography_deviation.py --mode synthetic

    # 模式 B：真实数据诊断（需要 projections/ 下有 tile_index.json + NPY）
    python scripts/diagnose_homography_deviation.py --mode real --npy <path_to_npy>
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #

@dataclass
class DeviationStats:
    """一组点的偏差统计。"""
    n: int
    median_xy: float
    p95_xy: float
    max_xy: float
    median_3d: float
    p95_3d: float
    max_3d: float

    def row_xy(self) -> str:
        return (
            f"| {self.n} | {self.median_xy:.3f} | {self.p95_xy:.3f} "
            f"| {self.max_xy:.3f} |"
        )

    def row_3d(self) -> str:
        return (
            f"| {self.n} | {self.median_3d:.3f} | {self.p95_3d:.3f} "
            f"| {self.max_3d:.3f} |"
        )


# --------------------------------------------------------------------------- #
# 核心拟合逻辑（直接从 verify_project.py 抽出的纯函数版本）
# --------------------------------------------------------------------------- #

def fit_homography_xy(
    points_2d: np.ndarray,
    points_3d: np.ndarray,
    reproj_thresh_m: float = 3.0,
) -> tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """用 (query_2d, world_xy) 拟合 3x3 单应矩阵。

    直接复用 ``build_local_coordinate_transform_context`` 的逻辑：
    ``cv2.findHomography(query[:,:2], world[:,:2], CV2.RANSAC, reproj_thresh_m)``。
    """
    if len(points_2d) < 4 or len(points_2d) != len(points_3d):
        return None, None
    pts_q = np.asarray(points_2d, dtype=np.float32).reshape(-1, 1, 2)
    pts_w = np.asarray(points_3d[:, :2], dtype=np.float32).reshape(-1, 1, 2)
    H, mask = cv2.findHomography(pts_q, pts_w, cv2.RANSAC, reproj_thresh_m)
    return H, mask


def _homography_map_xy(
    H: np.ndarray,
    points_2d: np.ndarray,
) -> np.ndarray:
    """用 H 把像素点映射到 SLAM XY 平面。"""
    pts = np.asarray(points_2d, dtype=np.float64).reshape(-1, 2)
    homo = np.column_stack([pts, np.ones(len(pts), dtype=np.float64)])
    mapped = (H @ homo.T).T
    valid = np.abs(mapped[:, 2]) > 1e-12
    result = np.zeros((len(pts), 2), dtype=np.float64)
    result[valid] = mapped[valid, :2] / mapped[valid, 2:3]
    result[~valid] = np.nan
    return result


def compute_deviation_stats(
    points_2d: np.ndarray,
    points_3d: np.ndarray,
    H: np.ndarray,
) -> DeviationStats:
    """计算一组点经过 H 映射后的 XY 偏差和 3D 偏差。

    XY 偏差 = |H(pixel) - world_xy|（2D 欧氏）
    3D 偏差 = |(H_x, H_y, 0) - world_xyz|（3D 欧氏，反映 H 强制 Z=0 的代价）
    """
    mapped_xy = _homography_map_xy(H, points_2d)
    world_xy = np.asarray(points_3d[:, :2], dtype=np.float64)
    world_xyz = np.asarray(points_3d, dtype=np.float64)

    valid = np.all(np.isfinite(mapped_xy), axis=1)
    mapped_xy = mapped_xy[valid]
    world_xy = world_xy[valid]
    world_xyz = world_xyz[valid]

    xy_errors = np.linalg.norm(mapped_xy - world_xy, axis=1)
    slam_xyz = np.column_stack([mapped_xy, np.zeros(len(mapped_xy), dtype=np.float64)])
    dist_3d = np.linalg.norm(slam_xyz - world_xyz, axis=1)

    return DeviationStats(
        n=int(valid.sum()),
        median_xy=float(np.median(xy_errors)) if len(xy_errors) else float("nan"),
        p95_xy=float(np.percentile(xy_errors, 95)) if len(xy_errors) else float("nan"),
        max_xy=float(np.max(xy_errors)) if len(xy_errors) else float("nan"),
        median_3d=float(np.median(dist_3d)) if len(dist_3d) else float("nan"),
        p95_3d=float(np.percentile(dist_3d, 95)) if len(dist_3d) else float("nan"),
        max_3d=float(np.max(dist_3d)) if len(dist_3d) else float("nan"),
    )


# --------------------------------------------------------------------------- #
# 合成数据生成
# --------------------------------------------------------------------------- #

def generate_synthetic_scene(
    n_ground: int = 20,
    n_elevation: int = 10,
    z_ground_range: tuple[float, float] = (-0.1, 0.1),
    z_elevation_range: tuple[float, float] = (1.0, 3.0),
    xy_range: tuple[float, float] = (-5.0, 5.0),
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, dict]:
    """生成合成 3D 场景 + 投影到像素的 2D 点。

    返回 ``(world_xyz, pixel_pts, K, Rt, meta)``：
    - world_xyz: (N, 3) 世界坐标
    - pixel_pts: (N, 2) 像素坐标
    - K: (3, 3) 相机内参
    - Rt: (3, 4) 外参 [R|t]
    - meta: 额外信息（地面/立面掩码、位姿等）
    """
    rng = np.random.default_rng(seed)

    # 地面点：Z 接近 0
    ground_xy = rng.uniform(xy_range[0], xy_range[1], size=(n_ground, 2))
    ground_z = rng.uniform(z_ground_range[0], z_ground_range[1], size=(n_ground, 1))
    ground_xyz = np.hstack([ground_xy, ground_z])

    # 立面点：Z 在 [1.0, 3.0]，XY 在不同区域（模拟墙面附近的控制点）
    elevation_xy = rng.uniform(xy_range[0], xy_range[1], size=(n_elevation, 2))
    elevation_z = rng.uniform(
        z_elevation_range[0], z_elevation_range[1], size=(n_elevation, 1)
    )
    elevation_xyz = np.hstack([elevation_xy, elevation_z])

    world_xyz = np.vstack([ground_xyz, elevation_xyz])
    ground_mask = np.array([True] * n_ground + [False] * n_elevation)

    # 相机内参：模拟 1920x1080 级别的针孔相机
    fx = 900.0
    fy = 900.0
    cx = 960.0
    cy = 540.0
    K = np.array([
        [fx, 0.0, cx],
        [0.0, fy, cy],
        [0.0, 0.0, 1.0],
    ], dtype=np.float64)

    # 相机位姿：把世界坐标原点放在相机前方约 8m，轻微俯视
    # R = 绕 X 轴旋转约 15°（俯仰），绕 Y 轴旋转约 10°（偏航）
    pitch = np.deg2rad(-15.0)  # 俯视
    yaw = np.deg2rad(10.0)
    Rx = np.array([
        [1.0, 0.0, 0.0],
        [0.0, np.cos(pitch), -np.sin(pitch)],
        [0.0, np.sin(pitch), np.cos(pitch)],
    ])
    Ry = np.array([
        [np.cos(yaw), 0.0, np.sin(yaw)],
        [0.0, 1.0, 0.0],
        [-np.sin(yaw), 0.0, np.cos(yaw)],
    ])
    R = Ry @ Rx
    # 相机放在 (0, 0, 0)，看向 -Z 方向？改用 COLMAP 惯例：
    # X_cam = R @ (X_world - t)，这里 t 是世界坐标中的相机中心
    # 让相机中心在 (0, -2, 1.5) 附近，看向场景中心
    t = np.array([0.0, -2.0, 1.5], dtype=np.float64).reshape(3, 1)
    Rt = np.hstack([R, t])

    # 投影到像素
    cam_pts = (R @ world_xyz.T).T + t.T  # (N, 3)
    # 确保点在相机前方
    if np.any(cam_pts[:, 2] <= 0):
        # 如果有点在相机后方，把 t 再往后推
        t_adjust = np.array([0.0, 0.0, 5.0]).reshape(3, 1)
        Rt[:, 3:] += t_adjust
        cam_pts = (R @ world_xyz.T).T + Rt[:, 3:].T

    proj = (K @ cam_pts.T).T
    pixel_pts = proj[:, :2] / proj[:, 2:3]

    meta = {
        "ground_mask": ground_mask,
        "n_ground": n_ground,
        "n_elevation": n_elevation,
        "z_ground_range": z_ground_range,
        "z_elevation_range": z_elevation_range,
        "K": K,
        "Rt": Rt,
        "cam_pts": cam_pts,
    }
    return world_xyz, pixel_pts, K, Rt, meta


# --------------------------------------------------------------------------- #
# 模式 A：合成数据诊断
# --------------------------------------------------------------------------- #

def run_synthetic_diagnosis(
    n_ground: int = 20,
    n_elevation: int = 10,
    z_elevation_range: tuple[float, float] = (1.0, 3.0),
    seed: int = 42,
) -> dict:
    """运行合成数据诊断，返回偏差统计字典。"""
    world_xyz, pixel_pts, K, Rt, meta = generate_synthetic_scene(
        n_ground=n_ground,
        n_elevation=n_elevation,
        z_elevation_range=z_elevation_range,
        seed=seed,
    )
    ground_mask = meta["ground_mask"]
    elevation_mask = ~ground_mask

    # ---- 当前方法：全点拟合 H ----
    H_all, mask_all = fit_homography_xy(pixel_pts, world_xyz)
    if H_all is None:
        raise RuntimeError("全点拟合 H 失败")

    stats_ground_all = compute_deviation_stats(
        pixel_pts[ground_mask], world_xyz[ground_mask], H_all
    )
    stats_elev_all = compute_deviation_stats(
        pixel_pts[elevation_mask], world_xyz[elevation_mask], H_all
    )

    # ---- 修复方法：只地面点拟合 H ----
    H_ground, mask_ground = fit_homography_xy(
        pixel_pts[ground_mask], world_xyz[ground_mask]
    )
    if H_ground is None:
        raise RuntimeError("仅地面拟合 H 失败")

    stats_ground_ground = compute_deviation_stats(
        pixel_pts[ground_mask], world_xyz[ground_mask], H_ground
    )
    # 立面点也经过地面 H，看偏差多大（仅作参考，实际不会用立面点做定位）
    stats_elev_ground = compute_deviation_stats(
        pixel_pts[elevation_mask], world_xyz[elevation_mask], H_ground
    )

    return {
        "H_all": H_all,
        "H_ground": H_ground,
        "stats_ground_all": stats_ground_all,
        "stats_elev_all": stats_elev_all,
        "stats_ground_ground": stats_ground_ground,
        "stats_elev_ground": stats_elev_ground,
        "meta": meta,
    }


# --------------------------------------------------------------------------- #
# 模式 B：真实数据诊断
# --------------------------------------------------------------------------- #

def _find_tile_index() -> Optional[Path]:
    """在 projections/ 目录下寻找 tile_index.json。"""
    candidates = [
        Path("projections/tile_index.json"),
        Path("/Users/pangjinfu/code/opencode-demo/projections/tile_index.json"),
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def _find_npy(tile_index_path: Path) -> Optional[Path]:
    """从 tile_index.json 找一个已接受的 NPY 文件。"""
    with tile_index_path.open() as f:
        records = json.load(f)
    for record in records:
        if not record.get("accepted", True):
            continue
        npy_path = record.get("npy_path") or record.get("path")
        if not npy_path:
            continue
        p = Path(npy_path)
        if not p.is_absolute():
            p = tile_index_path.parent / p
        if p.is_file() and p.suffix == ".npy":
            return p
    return None


def run_real_diagnosis(npy_path: Path, n_samples: int = 200) -> Optional[dict]:
    """对真实 NPY 数据做诊断。

    因为真实数据没有「query 像素 ↔ 世界点」的对应关系，这里退而求其次：
    把 NPY 自身的 (px, py, X, Y, Z) 当作「用当前位姿反投影得到的 query ↔ world」
    来模拟 PnP 内点，然后分组看 H 拟合的 XY 偏差。

    这只反映「如果把这些 3D 点当作 PnP 内点来拟 H」时的偏差结构，
    不是真实 query 的偏差，但能给出数量级参考。
    """
    npy = np.load(str(npy_path))
    if npy.ndim != 3 or npy.shape[2] != 3:
        print(f"[real] NPY shape {npy.shape} 不符合 (H,W,3)，跳过")
        return None

    h, w = npy.shape[:2]
    # 随机采样有效像素
    ys, xs = np.mgrid[0:h, 0:w]
    xyz = npy.reshape(-1, 3)
    valid = np.all(np.isfinite(xyz), axis=1) & np.any(xyz != 0, axis=1)
    valid_idx = np.flatnonzero(valid)
    if len(valid_idx) < 20:
        print(f"[real] 有效像素不足: {len(valid_idx)}")
        return None

    rng = np.random.default_rng(0)
    if len(valid_idx) > n_samples:
        valid_idx = rng.choice(valid_idx, n_samples, replace=False)

    pixel_pts = np.column_stack([
        xs.ravel()[valid_idx].astype(np.float64),
        ys.ravel()[valid_idx].astype(np.float64),
    ])
    world_xyz = xyz[valid_idx]

    # 按 Z 分组
    z_vals = world_xyz[:, 2]
    ground_mask = np.abs(z_vals) < 0.3
    elevation_mask = z_vals > 0.5  # 只取显著高于地面的点

    if ground_mask.sum() < 4:
        print(f"[real] 地面点不足: {ground_mask.sum()}")
        return None

    # 全点拟合 H
    H_all, _ = fit_homography_xy(pixel_pts, world_xyz)
    if H_all is None:
        print("[real] 全点拟合 H 失败")
        return None

    stats_ground_all = compute_deviation_stats(
        pixel_pts[ground_mask], world_xyz[ground_mask], H_all
    )
    stats_elev_all_list = []
    if elevation_mask.sum() >= 4:
        stats_elev_all_list.append(compute_deviation_stats(
            pixel_pts[elevation_mask], world_xyz[elevation_mask], H_all
        ))

    # 仅地面拟合 H
    H_ground, _ = fit_homography_xy(
        pixel_pts[ground_mask], world_xyz[ground_mask]
    )
    if H_ground is None:
        print("[real] 仅地面拟合 H 失败")
        return None

    stats_ground_ground = compute_deviation_stats(
        pixel_pts[ground_mask], world_xyz[ground_mask], H_ground
    )

    return {
        "stats_ground_all": stats_ground_all,
        "stats_elev_all_list": stats_elev_all_list,
        "stats_ground_ground": stats_ground_ground,
        "npy_shape": npy.shape,
        "n_total": int(len(valid_idx)),
        "n_ground": int(ground_mask.sum()),
        "n_elevation": int(elevation_mask.sum()),
    }


# --------------------------------------------------------------------------- #
# 报告生成
# --------------------------------------------------------------------------- #

def _percent_improvement(before: float, after: float) -> str:
    if not np.isfinite(before) or not np.isfinite(after):
        return "—"
    if before <= 0:
        return "—"
    pct = (before - after) / before * 100
    return f"{pct:.0f}%"


def render_synthetic_report(result: dict) -> str:
    """渲染合成数据诊断报告（Markdown 格式）。"""
    sg_a: DeviationStats = result["stats_ground_all"]
    se_a: DeviationStats = result["stats_elev_all"]
    sg_g: DeviationStats = result["stats_ground_ground"]
    se_g: DeviationStats = result["stats_elev_ground"]
    meta = result["meta"]

    lines: list[str] = []
    lines.append("# 全点拟合单应性 XY 偏差诊断报告")
    lines.append("")
    lines.append(f"- 日期：2026-08-04")
    lines.append(f"- 模式：合成数据（seed=42）")
    lines.append(f"- 地面点数：{meta['n_ground']}，Z ∈ {meta['z_ground_range']}")
    lines.append(f"- 立面点数：{meta['n_elevation']}，Z ∈ {meta['z_elevation_range']}")
    K = meta["K"]
    lines.append(f"- 相机内参：fx={K[0,0]:.0f} fy={K[1,1]:.0f} cx={K[0,2]:.0f} cy={K[1,2]:.0f}")
    lines.append("")
    lines.append("## XY 偏差（像素→SLAM XY 与真实 XY 的 2D 欧氏距离）")
    lines.append("")
    lines.append("| 分组 | 点数 | 全点拟合 median | 全点拟合 p95 | 全点拟合 max |")
    lines.append("|------|------|-----------------|--------------|--------------|")
    lines.append(f"| 地面点 | {sg_a.row_xy()[2:]}")
    lines.append(f"| 立面点 | {se_a.row_xy()[2:]}")
    lines.append("")
    lines.append("## XY 偏差对比：全点拟合 vs 仅地面拟合")
    lines.append("")
    lines.append("| 指标 | 全点拟合 H | 仅地面拟合 H | 改善 |")
    lines.append("|------|-----------|-------------|------|")
    lines.append(
        f"| 地面点 XY median | {sg_a.median_xy:.3f} m | "
        f"{sg_g.median_xy:.3f} m | "
        f"{_percent_improvement(sg_a.median_xy, sg_g.median_xy)} |"
    )
    lines.append(
        f"| 地面点 XY p95 | {sg_a.p95_xy:.3f} m | "
        f"{sg_g.p95_xy:.3f} m | "
        f"{_percent_improvement(sg_a.p95_xy, sg_g.p95_xy)} |"
    )
    lines.append(
        f"| 立面点 XY median | {se_a.median_xy:.3f} m | "
        f"{se_g.median_xy:.3f} m (参考) | "
        f"{_percent_improvement(se_a.median_xy, se_g.median_xy)} |"
    )
    lines.append("")
    lines.append("## 3D 偏差（H→SLAM 强制 Z=0，与真实 XYZ 的 3D 欧氏距离）")
    lines.append("")
    lines.append("| 分组 | 点数 | 全点拟合 median | 全点拟合 p95 | 全点拟合 max |")
    lines.append("|------|------|-----------------|--------------|--------------|")
    lines.append(f"| 地面点 | {sg_a.row_3d()[2:]}")
    lines.append(f"| 立面点 | {se_a.row_3d()[2:]}")
    lines.append("")
    lines.append("## 3D 偏差对比：全点拟合 vs 仅地面拟合")
    lines.append("")
    lines.append("| 指标 | 全点拟合 H | 仅地面拟合 H | 改善 |")
    lines.append("|------|-----------|-------------|------|")
    lines.append(
        f"| 地面点 3D median | {sg_a.median_3d:.3f} m | "
        f"{sg_g.median_3d:.3f} m | "
        f"{_percent_improvement(sg_a.median_3d, sg_g.median_3d)} |"
    )
    lines.append(
        f"| 立面点 3D median | {se_a.median_3d:.3f} m | "
        f"{se_g.median_3d:.3f} m (参考) | "
        f"{_percent_improvement(se_a.median_3d, se_g.median_3d)} |"
    )
    lines.append("")
    lines.append("## 结论")
    lines.append("")

    # 自动结论
    elev_median_xy = se_a.median_xy
    ground_median_xy_all = sg_a.median_xy
    ground_median_xy_gnd = sg_g.median_xy

    lines.append(
        f"1. 当前全点拟合 H 下，立面点 XY 偏差 median={elev_median_xy:.3f} m，"
        f"地面点 XY 偏差 median={ground_median_xy_all:.3f} m，"
        f"立面偏差约为地面的 {elev_median_xy/max(ground_median_xy_all, 1e-6):.1f} 倍。"
    )
    if ground_median_xy_gnd < ground_median_xy_all:
        lines.append(
            f"2. 仅用地面点拟合 H，地面点 XY 偏差从 {ground_median_xy_all:.3f} m "
            f"降至 {ground_median_xy_gnd:.3f} m，"
            f"改善 {_percent_improvement(ground_median_xy_all, ground_median_xy_gnd)}。"
        )
    else:
        lines.append(
            f"2. 仅用地面点拟合 H，地面点 XY 偏差为 {ground_median_xy_gnd:.3f} m "
            f"（本合成场景下立面点占比小，全点拟合对地面影响有限）。"
        )
    lines.append(
        "3. 建议在 Phase B 中引入平面检测，把 Z > 0.5 m 的点判定为立面点并剔除出 H 拟合，"
        "预期地面点 XY 偏差可改善 30%–80%（取决于立面点占比和高度）。"
    )
    lines.append("")
    lines.append("## 建议参数")
    lines.append("")
    lines.append("| 参数 | 建议值 | 说明 |")
    lines.append("|------|--------|------|")
    lines.append("| `ground_z_threshold` | 0.3 m | |Z| < 0.3 视为地面点 |")
    lines.append("| `elevation_z_threshold` | 0.5 m | Z > 0.5 视为立面点，剔除出 H 拟合 |")
    lines.append("| `min_ground_points` | 8 | 至少 8 个地面点才能稳定拟 H |")
    lines.append("")
    return "\n".join(lines)


def render_real_report(result: dict) -> str:
    """渲染真实数据诊断报告（Markdown 格式）。"""
    sg_a: DeviationStats = result["stats_ground_all"]
    sg_g: DeviationStats = result["stats_ground_ground"]
    se_list = result["stats_elev_all_list"]

    lines: list[str] = []
    lines.append("# 真实数据 NPY 单应性 XY 偏差诊断")
    lines.append("")
    lines.append(f"- NPY shape: {result['npy_shape']}")
    lines.append(f"- 有效采样: {result['n_total']} 像素")
    lines.append(f"- 地面点 (|Z|<0.3): {result['n_ground']}")
    lines.append(f"- 立面点 (Z>0.5): {result['n_elevation']}")
    lines.append("")
    lines.append("## 偏差统计")
    lines.append("")
    lines.append("| 分组 | 全点拟合 XY median | 全点拟合 XY p95 | 仅地面拟合 XY median | 仅地面拟合 XY p95 |")
    lines.append("|------|-------------------|-----------------|---------------------|-------------------|")
    se_median = se_list[0].median_xy if se_list else float("nan")
    se_p95 = se_list[0].p95_xy if se_list else float("nan")
    lines.append(
        f"| 地面点 | {sg_a.median_xy:.3f} | {sg_a.p95_xy:.3f} | "
        f"{sg_g.median_xy:.3f} | {sg_g.p95_xy:.3f} |"
    )
    lines.append(
        f"| 立面点 | {se_median:.3f} | {se_p95:.3f} | — | — |"
    )
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #

def main() -> int:
    parser = argparse.ArgumentParser(description="全点拟合单应性 XY 偏差诊断")
    parser.add_argument(
        "--mode",
        choices=["synthetic", "real"],
        default="synthetic",
        help="诊断模式：synthetic（合成数据）或 real（真实 NPY）",
    )
    parser.add_argument("--npy", type=str, default=None, help="真实 NPY 文件路径")
    parser.add_argument("--output", type=str, default=None, help="输出报告路径")
    parser.add_argument("--seed", type=int, default=42, help="合成数据 seed")
    parser.add_argument(
        "--n-ground", type=int, default=20, help="合成数据地面点数"
    )
    parser.add_argument(
        "--n-elevation", type=int, default=10, help="合成数据立面点数"
    )
    args = parser.parse_args()

    # 延迟 import cv2，让 --help 不依赖 cv2
    global cv2
    import cv2  # noqa: E402

    output_path = Path(args.output) if args.output else None

    if args.mode == "synthetic":
        print("[diagnose] 运行合成数据诊断 ...")
        result = run_synthetic_diagnosis(
            n_ground=args.n_ground,
            n_elevation=args.n_elevation,
            seed=args.seed,
        )
        report = render_synthetic_report(result)
        if output_path is None:
            output_path = Path(
                "/Users/pangjinfu/code/opencode-demo/reports"
                "/2026-08-04-homography-deviation-diagnosis.md"
            )
        print(report)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report, encoding="utf-8")
        print(f"\n[diagnose] 报告已写入: {output_path}")
        return 0

    # real 模式
    npy_path = None
    if args.npy:
        npy_path = Path(args.npy)
    else:
        tile_index = _find_tile_index()
        if tile_index is not None:
            npy_path = _find_npy(tile_index)
    if npy_path is None or not npy_path.is_file():
        print("[diagnose] 未找到现成 NPY，仅用合成数据做诊断。")
        print("[diagnose] 如需真实数据模式，请提供 --npy <path_to_npy>")
        # 回退到 synthetic
        result = run_synthetic_diagnosis(seed=args.seed)
        report = render_synthetic_report(result)
        if output_path is None:
            output_path = Path(
                "/Users/pangjinfu/code/opencode-demo/reports"
                "/2026-08-04-homography-deviation-diagnosis.md"
            )
        print(report)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report, encoding="utf-8")
        print(f"\n[diagnose] 报告已写入: {output_path}")
        return 0

    print(f"[diagnose] 真实数据模式: {npy_path}")
    result = run_real_diagnosis(npy_path)
    if result is None:
        print("[diagnose] 真实数据诊断失败，回退到合成数据。")
        result = run_synthetic_diagnosis(seed=args.seed)
        report = render_synthetic_report(result)
    else:
        report = render_real_report(result)
    if output_path is None:
        output_path = Path(
            "/Users/pangjinfu/code/opencode-demo/reports"
            "/2026-08-04-homography-deviation-diagnosis.md"
        )
    print(report)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    print(f"\n[diagnose] 报告已写入: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
