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
    distance_threshold: float = 0.2,
    min_inliers: int = 4,
    max_iterations: int = 1000,
    seed: int = 1337,
) -> Tuple[Optional[Tuple[float, float, float, float]], Optional[np.ndarray]]:
    """RANSAC 拟合最佳平面，返回 (plane_params, inlier_mask)。

    参数
    ----------
    points_3d : (N, 3) 世界坐标点
    distance_threshold : 点到平面距离阈值（米），默认 0.2
    min_inliers : 最少内点数，默认 4
    max_iterations : RANSAC 最大迭代次数，默认 1000
    seed : 随机种子，保证可复现

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
        distances = np.abs(pts @ normal + d)

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
    refined_distances = np.abs(pts @ refined_normal + refined_d)
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
