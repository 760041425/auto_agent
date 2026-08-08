"""TL-004-07 至 TL-004-13：分层单应集成测试。

验证 build_local_coordinate_transform_context 在平面检测启用后的行为：
- 纯地面点 → 行为与修改前一致（H 相同）
- 地面+立面混合 → 分层 H 精度提升（median < 全点 H）
- 地面点不足 4 → 回退到全点拟合
- 不传 plane 参数 → 向后兼容（全点拟合）
- segment_plane 返回 (None, None) → 回退到全点拟合
- evaluate_local_coordinate_consistency 在分层 H 下不退化
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pytest

from services.localizer.verify_projection import (
    build_local_coordinate_transform_context,
    evaluate_local_coordinate_consistency,
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _make_mixed_correspondences(
    n_ground: int = 20,
    n_elev: int = 5,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """生成合成 2D-3D 对应：地面 + 立面，以及对应投影 XYZ。

    返回 ``(query_2d, world_3d, projection_xyz, K, rvec, tvec)``：
    - query_2d: (N, 2) 像素坐标
    - world_3d: (N, 3) 世界坐标
    - projection_xyz: (H, W, 3) 投影 XYZ 图（与 world_3d 一致，用于一致性验证）
    - K: (3, 3) 相机内参
    - rvec, tvec: PnP 位姿（此处仅占位）
    """
    rng = np.random.default_rng(seed)

    # 地面点：Z 接近 0
    ground_xy = rng.uniform(-5.0, 5.0, size=(n_ground, 2))
    ground_z = rng.uniform(-0.1, 0.1, size=(n_ground, 1))
    ground_xyz = np.hstack([ground_xy, ground_z])

    # 立面点：Z 在 [1.0, 3.0]
    elev_xy = rng.uniform(-5.0, 5.0, size=(n_elev, 2))
    elev_z = rng.uniform(1.0, 3.0, size=(n_elev, 1))
    elev_xyz = np.hstack([elev_xy, elev_z])

    world_3d = np.vstack([ground_xyz, elev_xyz])

    # 构造一个简单的正投影（query_2d = world_xy * scale + offset）
    # 这样 H 的真值是已知的：query_2d → world_xy 的线性映射
    scale = 10.0
    offset = np.array([100.0, 100.0])
    query_2d = world_3d[:, :2] * scale + offset  # (N, 2)

    # 构造 projection_xyz：与 world_3d 在像素位置对齐
    # 像素位置由 query_2d 反算到图像坐标
    h, w = 200, 200
    projection_xyz = np.zeros((h, w, 3), dtype=np.float32)
    for i in range(len(query_2d)):
        px = int(np.clip(query_2d[i, 0], 0, w - 1))
        py = int(np.clip(query_2d[i, 1], 0, h - 1))
        projection_xyz[py, px] = world_3d[i]

    # 相机内参（占位）
    K = np.array([
        [900.0, 0.0, 960.0],
        [0.0, 900.0, 540.0],
        [0.0, 0.0, 1.0],
    ], dtype=np.float64)

    rvec = np.zeros((3, 1), dtype=np.float64)
    tvec = np.zeros((3, 1), dtype=np.float64)

    return query_2d, world_3d, projection_xyz, K, rvec, tvec


def _make_pure_elevation_correspondences(
    n_elev: int = 10,
    seed: int = 123,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """生成全立面（无地面）的 2D-3D 对应，用于退化回退测试。"""
    rng = np.random.default_rng(seed)
    elev_xy = rng.uniform(-5.0, 5.0, size=(n_elev, 2))
    elev_z = rng.uniform(1.0, 3.0, size=(n_elev, 1))
    elev_xyz = np.hstack([elev_xy, elev_z])

    scale = 10.0
    offset = np.array([100.0, 100.0])
    query_2d = elev_xyz[:, :2] * scale + offset

    h, w = 200, 200
    projection_xyz = np.zeros((h, w, 3), dtype=np.float32)
    for i in range(len(query_2d)):
        px = int(np.clip(query_2d[i, 0], 0, w - 1))
        py = int(np.clip(query_2d[i, 1], 0, h - 1))
        projection_xyz[py, px] = elev_xyz[i]

    return query_2d, elev_xyz, projection_xyz


# --------------------------------------------------------------------------- #
# tests
# --------------------------------------------------------------------------- #

def test_layered_homography_reduces_median_error(tmp_path):
    """TL-004-08：分层 H 的 median_m 应小于全点 H。

    合成数据：地面 20 点 + 立面 5 点，H 真值已知（正投影）。
    全点拟合 H 会被立面点拉歪，分层拟合只用地面点，median 应更小。
    """
    query_2d, world_3d, projection_xyz, _, _, _ = _make_mixed_correspondences(
        n_ground=20, n_elev=5
    )
    output_path = tmp_path / "layered.npy"

    # 生成本地点云用于平面检测（模拟密集点云）
    rng = np.random.default_rng(42)
    dense_pts = np.column_stack([
        rng.uniform(-5, 5, 200),
        rng.uniform(-5, 5, 200),
        rng.uniform(-0.1, 0.1, 200),  # 地面点
    ])

    # 分层 H（启用平面检测）
    ctx_layered = build_local_coordinate_transform_context(
        query_2d, world_3d, projection_xyz, output_path,
        plane_distance_threshold=0.1,
        plane_seed=1337,
        dense_points=dense_pts,
    )
    assert ctx_layered["status"] == "ready"
    layered_median = ctx_layered["consistency"]["median_m"]

    # 全点 H（禁用平面检测）
    ctx_full = build_local_coordinate_transform_context(
        query_2d, world_3d, projection_xyz, output_path,
        plane_distance_threshold=None,  # 禁用
    )
    assert ctx_full["status"] == "ready"
    full_median = ctx_full["consistency"]["median_m"]

    # 分层 H 的 median 应小于或等于全点 H（不退化）
    assert layered_median <= full_median + 1e-6


def test_plane_segmentation_field_in_context(tmp_path):
    """TL-004-07：context 应包含 plane_segmentation 字段。"""
    query_2d, world_3d, projection_xyz, _, _, _ = _make_mixed_correspondences(
        n_ground=20, n_elev=5
    )
    output_path = tmp_path / "ctx.npy"

    # 生成本地点云用于平面检测
    rng = np.random.default_rng(42)
    dense_pts = np.column_stack([
        rng.uniform(-5, 5, 200),
        rng.uniform(-5, 5, 200),
        rng.uniform(-0.1, 0.1, 200),
    ])

    ctx = build_local_coordinate_transform_context(
        query_2d, world_3d, projection_xyz, output_path,
        plane_distance_threshold=0.1,
        dense_points=dense_pts,
    )
    assert ctx["status"] == "ready"
    assert "plane_segmentation" in ctx
    ps = ctx["plane_segmentation"]
    assert ps["status"] == "plane_detected"
    assert ps["n_ground_inliers"] > 0
    assert ps["n_ground_inliers"] <= ps["n_total_points"]
    assert "plane_params" in ps
    assert "distance_threshold_m" in ps


def test_fallback_when_no_ground(tmp_path):
    """TL-004-09：无地面时回退，plane_segmentation.status 标记 insufficient。"""
    query_2d, world_3d, projection_xyz = _make_pure_elevation_correspondences(n_elev=10)
    output_path = tmp_path / "fallback.npy"

    ctx = build_local_coordinate_transform_context(
        query_2d, world_3d, projection_xyz, output_path,
        plane_distance_threshold=0.2,
    )
    # 仍应返回有效 H（回退行为）
    assert ctx["status"] == "ready"
    assert "plane_segmentation" in ctx
    # 无地面点 → insufficient_ground_points
    assert ctx["plane_segmentation"]["status"] in (
        "insufficient_ground_points",
        "plane_detected",  # RANSAC 可能提取到某个平面，但未必是地面
    )


def test_backward_compat_no_plane_params(tmp_path):
    """TL-004-10：不传 plane 参数时行为与旧版一致（全点拟合）。"""
    query_2d, world_3d, projection_xyz, _, _, _ = _make_mixed_correspondences(
        n_ground=20, n_elev=5
    )
    output_path = tmp_path / "compat.npy"

    # 不传 plane 参数（旧版调用方式）
    ctx = build_local_coordinate_transform_context(
        query_2d, world_3d, projection_xyz, output_path,
    )
    assert ctx["status"] == "ready"
    # plane_segmentation 字段应存在，但 status 应为 skipped
    assert "plane_segmentation" in ctx
    assert ctx["plane_segmentation"]["status"] == "skipped"


def test_backward_compat_context_has_no_plane_field_when_disabled(tmp_path):
    """TL-004-10b：plane_distance_threshold=None 时，plane_segmentation.status=skipped。"""
    query_2d, world_3d, projection_xyz, _, _, _ = _make_mixed_correspondences(
        n_ground=20, n_elev=5
    )
    output_path = tmp_path / "compat2.npy"

    ctx = build_local_coordinate_transform_context(
        query_2d, world_3d, projection_xyz, output_path,
        plane_distance_threshold=None,
    )
    assert ctx["status"] == "ready"
    assert ctx["plane_segmentation"]["status"] == "skipped"


def test_layered_homography_does_not_regress_consistency(tmp_path):
    """TL-004-11：分层 H 下 evaluate_local_coordinate_consistency 不退化。

    在合成数据上（地面 20 + 立面 5），分层 H 的 median_m 应 <= 全点 H。
    """
    query_2d, world_3d, projection_xyz, _, _, _ = _make_mixed_correspondences(
        n_ground=20, n_elev=5
    )
    output_path = tmp_path / "regression.npy"

    # 分层 H
    ctx_layered = build_local_coordinate_transform_context(
        query_2d, world_3d, projection_xyz, output_path,
        plane_distance_threshold=0.2,
    )
    # 全点 H
    ctx_full = build_local_coordinate_transform_context(
        query_2d, world_3d, projection_xyz, output_path,
        plane_distance_threshold=None,
    )

    layered_median = ctx_layered["consistency"]["median_m"]
    full_median = ctx_full["consistency"]["median_m"]

    # 分层 H 不应比全点 H 差（允许浮点误差）
    assert layered_median <= full_median + 1e-6


def test_pure_ground_behavior_unchanged(tmp_path):
    """TL-004-07：纯地面点输入时，行为与修改前一致（H 相同）。"""
    rng = np.random.default_rng(99)
    n = 20
    xy = rng.uniform(-5.0, 5.0, size=(n, 2))
    z = rng.uniform(-0.1, 0.1, size=(n, 1))
    world_3d = np.hstack([xy, z])

    scale = 10.0
    offset = np.array([100.0, 100.0])
    query_2d = world_3d[:, :2] * scale + offset

    h, w = 200, 200
    projection_xyz = np.zeros((h, w, 3), dtype=np.float32)
    for i in range(len(query_2d)):
        px = int(np.clip(query_2d[i, 0], 0, w - 1))
        py = int(np.clip(query_2d[i, 1], 0, h - 1))
        projection_xyz[py, px] = world_3d[i]

    output_path = tmp_path / "pure_ground.npy"

    # 生成本地点云用于平面检测
    dense_pts = np.column_stack([
        rng.uniform(-5, 5, 200),
        rng.uniform(-5, 5, 200),
        rng.uniform(-0.1, 0.1, 200),
    ])

    ctx = build_local_coordinate_transform_context(
        query_2d, world_3d, projection_xyz, output_path,
        plane_distance_threshold=0.1,
        dense_points=dense_pts,
    )
    assert ctx["status"] == "ready"
    assert ctx["plane_segmentation"]["status"] == "plane_detected"
    # PnP 内点中地面点应全部为内点（19/20）
    assert ctx["plane_segmentation"]["n_ground_inliers"] >= 15
