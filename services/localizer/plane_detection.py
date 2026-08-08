"""RANSAC 平面分割：从 PnP 内点中提取地面平面内点。

当 PnP 内点混合地面与立面点时，直接拟合单应矩阵 H 会被立面点拉歪。
本模块提供 segment_plane() 做 RANSAC 平面分割，返回最大平面（地面）的
平面参数和内点掩码，供调用方在拟 H 之前过滤出地面子集。

设计决策（DEC-004-01）：
- 使用 RANSAC（非 Hough/区域生长），对单平面提取最简单
- 纯 numpy 实现，零新依赖
- 固定随机种子（默认 1337）保证可复现
- 3 点采样时检查共线性（法向量模长 <1e-8 则跳过）
- 精化阶段用最小二乘（SVD 最小特征向量）重新拟合
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

import numpy as np

_logger = logging.getLogger("localizer.plane_detection")


def segment_plane(
    points_3d: np.ndarray,
    *,
    distance_threshold: float = 0.1,
    min_inliers: int = 100,
    max_iterations: int = 2000,
    seed: int = 1337,
    use_z_xy_fit: bool = True,
) -> Tuple[Optional[Tuple[float, float, float, float]], Optional[np.ndarray]]:
    """RANSAC 拟合最佳平面，返回 (plane_params, inlier_mask)。

    对齐 slam-map 的 detect_ground_plane_ransac 方法：
    - 默认阈值 0.1m（严格）
    - 最少 100 个内点
    - 可选 Z=f(X,Y) 约束（适合地面平面）

    参数
    ----------
    points_3d : (N, 3) 世界坐标点
    distance_threshold : 点到平面距离阈值（米），默认 0.1
    min_inliers : 最少内点数，默认 100
    max_iterations : RANSAC 最大迭代次数，默认 2000
    seed : 随机种子，保证可复现
    use_z_xy_fit : 是否使用 Z=f(X,Y) 约束（推荐地面检测）

    返回
    -------
    (plane_params, inlier_mask) — ``plane_params=(a, b, c, d)`` 归一化平面
    ``ax + by + cz + d = 0``；``inlier_mask`` 为长度 N 的 bool 数组。
    失败返回 ``(None, None)``。
    """
    # 1. 校验输入
    pts = np.asarray(points_3d, dtype=np.float64).reshape(-1, 3)
    if pts.shape[0] < 3:
        return None, None
    if not np.all(np.isfinite(pts)):
        return None, None

    rng = np.random.default_rng(seed)
    n = len(pts)
    best_inlier_count = 0
    best_inlier_mask: Optional[np.ndarray] = None
    best_plane: Optional[np.ndarray] = None

    # 2. RANSAC 循环
    # 优先尝试 sklearn RANSAC（更稳定，与 slam-map 一致）
    if use_z_xy_fit:
        result = _segment_plane_sklearn(pts, distance_threshold, min_inliers, max_iterations, seed)
        if result[0] is not None:
            return result

    # 回退：自定义 RANSAC
    for _ in range(max_iterations):
        # 随机采 3 点
        sample_idx = rng.choice(n, size=3, replace=False)
        p1, p2, p3 = pts[sample_idx[0]], pts[sample_idx[1]], pts[sample_idx[2]]

        # 计算法向量 n = (p2-p1) × (p3-p1)
        v1 = p2 - p1
        v2 = p3 - p1
        normal = np.cross(v1, v2)
        norm_len = np.linalg.norm(normal)

        # 3 点共线检查
        if norm_len < 1e-8:
            continue

        # 归一化法向量
        normal = normal / norm_len
        a, b, c = normal

        # d = -n·p_center — 使平面穿过采样 3 点的质心
        sample_center = (p1 + p2 + p3) / 3.0
        d = -np.dot(normal, sample_center)

        # 计算所有点到平面距离 |points @ n + d|（法向量已归一化，无需再除）
        distances = np.abs(pts @ normal.flatten() + float(d))

        # 统计内点（距离 < distance_threshold）
        inlier_mask = distances < distance_threshold
        inlier_count = int(inlier_mask.sum())

        # 保留内点最多的模型
        if inlier_count > best_inlier_count:
            best_inlier_count = inlier_count
            best_inlier_mask = inlier_mask
            best_plane = np.array([a, b, c, d], dtype=np.float64)

    # 3. 检查是否满足最少内点要求
    if best_inlier_mask is None or best_inlier_count < min_inliers:
        return None, None

    # 4. 对内点最小二乘重新拟合平面（精化）
    inlier_pts = pts[best_inlier_mask]
    centroid = inlier_pts.mean(axis=0)
    centered = inlier_pts - centroid
    # SVD：最小特征值对应的特征向量即为精确法向量
    _, s, Vt = np.linalg.svd(centered, full_matrices=False)
    # 最小奇异值对应 Vt 最后一行
    refined_normal = Vt[-1]
    # 确保法向量方向一致（与 RANSAC 方向同号，避免翻转）
    if best_plane is not None:
        if np.dot(refined_normal, best_plane[:3]) < 0:
            refined_normal = -refined_normal
    refined_d = -np.dot(refined_normal, centroid)

    # 归一化（SVD 返回的已是单位向量，但保险起见再归一化一次）
    norm = np.linalg.norm(refined_normal)
    if norm < 1e-12:
        return None, None
    refined_normal = refined_normal / norm
    refined_d = refined_d / norm

    # 用精化后的平面重新计算内点掩码（更精确）
    refined_distances = np.abs(pts @ refined_normal.flatten() + float(refined_d))
    refined_inlier_mask = refined_distances < distance_threshold
    refined_inlier_count = int(refined_inlier_mask.sum())
    if refined_inlier_count < min_inliers:
        # 精化后退化，回退到 RANSAC 结果
        refined_inlier_mask = best_inlier_mask

    plane_params = (
        float(refined_normal[0]),
        float(refined_normal[1]),
        float(refined_normal[2]),
        float(refined_d),
    )
    return plane_params, refined_inlier_mask


def _segment_plane_sklearn(
    pts: np.ndarray,
    distance_threshold: float,
    min_inliers: int,
    max_iterations: int,
    seed: int,
) -> Tuple[Optional[Tuple[float, float, float, float]], Optional[np.ndarray]]:
    """sklearn RANSAC 拟合地面平面（对齐 slam-map 方法）。

    使用 Z=f(X,Y) 约束，适合地面平面检测。
    """
    try:
        from sklearn.linear_model import RANSACRegressor, LinearRegression
    except ImportError:
        return None, None

    if len(pts) < min_inliers:
        return None, None

    # Z = f(X,Y) 约束
    X = pts[:, :2]  # Nx2
    z = pts[:, 2]   # N

    ransac = RANSACRegressor(
        LinearRegression(),
        min_samples=3,
        residual_threshold=distance_threshold,
        max_trials=max_iterations,
        random_state=seed,
    )

    try:
        ransac.fit(X, z)
        inlier_mask = ransac.inlier_mask_
        n_ground = int(inlier_mask.sum())

        if n_ground < min_inliers:
            return None, None

        coef = ransac.estimator_.coef_
        intercept = ransac.estimator_.intercept_

        # 平面方程: z = coef[0]*x + coef[1]*y + intercept
        # 标准形式: coef[0]*x + coef[1]*y - z + intercept = 0
        plane_eq = np.array([coef[0], coef[1], -1.0, intercept])
        norm = np.linalg.norm(plane_eq[:3])
        if norm > 0:
            plane_eq = plane_eq / norm

        return tuple(float(p) for p in plane_eq), inlier_mask
    except Exception:
        return None, None


def build_plane_coordinate_frame(
    plane_params: Tuple[float, float, float, float],
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """从平面方程构建平面上的 2D 坐标系。

    返回 ``(origin, x_axis, y_axis)``：
    - ``origin``: 平面上距离世界原点最近的点（3，）
    - ``x_axis``: 平面内单位切向量（3，）
    - ``y_axis``: 平面内单位切向量，与 x_axis 正交（3，）

    对于地面平面（法向量 ≈ [0,0,1]），x/y_axis 大致与 world X/Y 对齐；
    对于倾斜平面，x/y_axis 自动适应平面方向。
    """
    a, b, c, d = plane_params
    normal = np.array([a, b, c], dtype=np.float64)
    normal_norm = np.linalg.norm(normal)
    if normal_norm < 1e-12:
        raise ValueError("Degenerate plane normal")
    normal = normal / normal_norm
    d_norm = d / normal_norm

    # 平面距世界原点最近的点：t = -d / ||n||^2（n 已单位化即 -d）
    origin = -d_norm * normal

    # 选参考方向：若法向量不接近 Z 轴，用 world Z；否则用 world X
    if abs(normal[2]) < 0.9:
        ref = np.array([0.0, 0.0, 1.0])
    else:
        ref = np.array([1.0, 0.0, 0.0])

    # x_axis = ref 在平面上的投影（垂直于法向量）
    x_axis = ref - np.dot(ref, normal) * normal
    x_len = np.linalg.norm(x_axis)
    if x_len < 1e-8:
        # ref 与法向量几乎平行，换一个参考方向
        ref2 = np.array([0.0, 1.0, 0.0])
        x_axis = ref2 - np.dot(ref2, normal) * normal
        x_len = np.linalg.norm(x_axis)
    x_axis = x_axis / x_len

    # y_axis = x_axis × normal（保证右手系且与 world +Y 对齐）
    # 对于地面平面（normal ≈ [0,0,1] 或 [0,0,-1]）：
    #   x_axis ≈ [1,0,0], y_axis ≈ [0,1,0]（与 world +Y 同向）
    y_axis = np.cross(x_axis, normal)
    y_axis = y_axis / np.linalg.norm(y_axis)

    # 验证：地面平面时 y_axis 应接近 world +Y
    if abs(normal[2]) > 0.9 and y_axis[1] < 0:
        y_axis = -y_axis

    return origin, x_axis, y_axis


def project_points_to_plane(
    points_3d: np.ndarray,
    plane_params: Tuple[float, float, float, float],
) -> np.ndarray:
    """把 3D 点投影到平面上，返回平面坐标系下的 2D 坐标。

    参数
    ----------
    points_3d : (N, 3)
    plane_params : (a, b, c, d) 归一化平面方程 ax+by+cz+d=0

    返回
    -------
    plane_coords : (N, 2) — 每行是 (u, v) 平面坐标（单位与输入一致，米）
    """
    origin, x_axis, y_axis = build_plane_coordinate_frame(plane_params)
    pts = np.asarray(points_3d, dtype=np.float64).reshape(-1, 3)
    centered = pts - origin
    u = centered @ x_axis
    v = centered @ y_axis
    return np.column_stack([u, v])
