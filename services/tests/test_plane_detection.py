"""TL-004-01 至 TL-004-06：平面检测单元测试。

验证 segment_plane 的 RANSAC 平面分割行为：
- 纯地面点 → 全部内点
- 地面+立面混合 → 只留地面
- 点数不足 4 → 退化返回 (None, None)
- NaN/Inf 输入 → 退化返回 (None, None)
- 确定性：同输入同 seed 多次调用返回相同结果
- 阈值敏感：0.15m 能分离地面与立面；0.3m 阈值过宽时全部内点
- 斜面输入 → 正确提取斜面内点
"""

from __future__ import annotations

import numpy as np
import pytest

from services.localizer.plane_detection import segment_plane


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #

def _pure_ground_points(n: int = 20, seed: int = 42) -> np.ndarray:
    """Z ∈ [-0.1, 0.1] 的纯地面点，XY 随机。"""
    rng = np.random.default_rng(seed)
    xy = rng.uniform(-5.0, 5.0, size=(n, 2))
    z = rng.uniform(-0.1, 0.1, size=(n, 1))
    return np.hstack([xy, z])


def _mixed_ground_elevation_points(
    n_ground: int = 20,
    n_elev: int = 10,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray]:
    """返回混合点数组 + 地面掩码（前 n_ground 个为 True）。"""
    rng = np.random.default_rng(seed)
    ground_xy = rng.uniform(-5.0, 5.0, size=(n_ground, 2))
    ground_z = rng.uniform(-0.1, 0.1, size=(n_ground, 1))
    ground = np.hstack([ground_xy, ground_z])

    elev_xy = rng.uniform(-5.0, 5.0, size=(n_elev, 2))
    elev_z = rng.uniform(1.0, 3.0, size=(n_elev, 1))
    elev = np.hstack([elev_xy, elev_z])

    points = np.vstack([ground, elev])
    ground_mask = np.array([True] * n_ground + [False] * n_elev)
    return points, ground_mask


# --------------------------------------------------------------------------- #
# tests
# --------------------------------------------------------------------------- #

def test_segment_plane_pure_ground():
    """TL-004-01：纯地面点 → 全部内点。"""
    points = _pure_ground_points(n=20)
    plane_params, inlier_mask = segment_plane(points)

    assert plane_params is not None
    assert inlier_mask is not None
    assert len(inlier_mask) == len(points)
    assert bool(inlier_mask.all())  # 全部 True

    a, b, c, d = plane_params
    norm = np.sqrt(a * a + b * b + c * c)
    assert abs(norm - 1.0) < 1e-6  # 归一化
    # 法向量应接近 (0, 0, ±1)
    assert abs(abs(c) - 1.0) < 0.3


def test_segment_plane_mixed_ground_elevation():
    """TL-004-02：地面+立面混合 → 只留地面。"""
    points, ground_mask = _mixed_ground_elevation_points(n_ground=20, n_elev=10)
    plane_params, inlier_mask = segment_plane(points, distance_threshold=0.2)

    assert plane_params is not None
    assert inlier_mask is not None
    n_inliers = int(inlier_mask.sum())
    n_ground = int(ground_mask.sum())
    # 地面内点占比应 >70%，且立面点大部分被剔除
    assert n_inliers >= int(0.7 * n_ground)
    # 立面点应大部分为 outlier
    elev_inliers = int(inlier_mask[n_ground:].sum())
    assert elev_inliers <= 2  # 最多 2 个立面点误纳


def test_segment_plane_insufficient_points():
    """TL-004-03：点数 <4 → 退化返回 (None, None)。"""
    rng = np.random.default_rng(0)
    pts = rng.uniform(-1.0, 1.0, size=(3, 3))
    plane_params, inlier_mask = segment_plane(pts)
    assert plane_params is None
    assert inlier_mask is None


def test_segment_plane_nan_input():
    """TL-004-04：全 NaN 输入 → 退化返回 (None, None)，不抛异常。"""
    pts = np.full((10, 3), np.nan)
    plane_params, inlier_mask = segment_plane(pts)
    assert plane_params is None
    assert inlier_mask is None


def test_segment_plane_deterministic():
    """TL-004-05：同输入、同 seed 多次调用返回完全相同结果。"""
    points, _ = _mixed_ground_elevation_points(n_ground=20, n_elev=5)
    result_a = segment_plane(points, seed=1337)
    result_b = segment_plane(points, seed=1337)
    result_c = segment_plane(points, seed=1337)

    pa, ma = result_a
    pb, mb = result_b
    pc, mc = result_c

    assert pa is not None and pb is not None and pc is not None
    np.testing.assert_array_equal(pa, pb)
    np.testing.assert_array_equal(pa, pc)
    np.testing.assert_array_equal(ma, mb)
    np.testing.assert_array_equal(ma, mc)


def test_segment_plane_threshold_sensitivity():
    """TL-004-06：阈值敏感 — 0.15m 分离地面与立面；0.3m 地面全内点。"""
    points, ground_mask = _mixed_ground_elevation_points(n_ground=20, n_elev=10)
    n_ground = int(ground_mask.sum())

    # 窄阈值：应只留地面
    _, inlier_narrow = segment_plane(points, distance_threshold=0.15)
    assert inlier_narrow is not None
    # 地面点应保留，立面点大部分剔除
    ground_inliers_narrow = int(inlier_narrow[:n_ground].sum())
    elev_inliers = int(inlier_narrow[n_ground:].sum())
    assert ground_inliers_narrow >= int(0.8 * n_ground)
    assert elev_inliers <= 2

    # 宽阈值（0.3m）：地面平整度 ±0.1m 下，所有地面点都是内点
    _, inlier_wide = segment_plane(points, distance_threshold=0.3)
    assert inlier_wide is not None
    ground_inliers_wide = int(inlier_wide[:n_ground].sum())
    # 0.3m 阈值对 ±0.1m 地面平整度足够宽容，地面点应全部保留
    assert ground_inliers_wide >= int(0.95 * n_ground)


def test_segment_plane_sloped_plane():
    """TL-004-013：斜面输入（Z = X*0.1 + 噪声）→ 正确提取斜面内点。"""
    rng = np.random.default_rng(7)
    n = 30
    x = rng.uniform(-5.0, 5.0, size=(n,))
    y = rng.uniform(-5.0, 5.0, size=(n,))
    z = x * 0.1 + rng.normal(0.0, 0.05, size=(n,))  # 斜面 + 噪声
    points = np.column_stack([x, y, z])

    plane_params, inlier_mask = segment_plane(points, distance_threshold=0.2)
    assert plane_params is not None
    assert inlier_mask is not None
    # 至少 70% 内点
    assert int(inlier_mask.sum()) >= int(0.7 * n)

    # 法向量应与理论值（斜面法向量 ∝ (-0.1, 0, 1)）误差 <5°
    a, b, c, d = plane_params
    normal = np.array([a, b, c])
    theoretical = np.array([-0.1, 0.0, 1.0])
    theoretical /= np.linalg.norm(theoretical)
    cos_angle = abs(np.dot(normal, theoretical))
    cos_angle = min(cos_angle, 1.0)
    angle_rad = np.arccos(cos_angle)
    angle_deg = np.degrees(angle_rad)
    assert angle_deg < 5.0, f"法向量误差 {angle_deg:.2f}° > 5°"
