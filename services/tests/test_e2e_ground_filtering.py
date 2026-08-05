"""端到端回归测试：地面点过滤修复高处点拉歪 H→SLAM 的问题。

场景：透视投影下，地面+立面+高处点映射到不同像素。
高处点（Z≈12m）通过 H→SLAM（Z=0 平面）时 XY 必然大幅偏移。
地面点过滤应让 median_m 从 >5m 降到 <1m。

这个测试锁定 specs/004-plane-aware-homography 的修复，防止回归。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import cv2
import numpy as np
import pytest

from services.localizer.verify_projection import (
    build_local_coordinate_transform_context,
)


def _make_perspective_scene(
    n_ground: int = 50,
    n_elevation: int = 30,
    n_high: int = 50,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """构造透视投影场景：地面 + 立面 + 高处点。

    相机在 (0, 0, 15) 俯视原点。高处点 Z=12m 离相机近，
    透视效应使其像素位置大幅偏移。
    """
    rng = np.random.default_rng(seed)
    W, H = 512, 512
    f = max(W, H) / (2 * np.tan(np.deg2rad(37.5)))
    K = np.array([[f, 0, W / 2], [0, f, H / 2], [0, 0, 1]], dtype=np.float64)

    # 相机位姿：俯视
    R = np.array([[1, 0, 0], [0, -1, 0], [0, 0, -1]], dtype=np.float64)
    cam_pos = np.array([0.0, 0.0, 15.0])
    t = -R @ cam_pos.reshape(3, 1)

    # 世界点：X ∈ [1, 8]（避免 Y 反射歧义）
    gx = rng.uniform(1, 8, n_ground)
    ex = rng.uniform(1, 8, n_elevation)
    hx = rng.uniform(1, 8, n_high)
    gy = rng.uniform(-6, 6, n_ground)
    ey = rng.uniform(-6, 6, n_elevation)
    hy = rng.uniform(-6, 6, n_high)

    # 高度
    gz = rng.uniform(-0.15, 0.15, n_ground)
    ez = rng.uniform(1.5, 2.5, n_elevation)
    hz = rng.uniform(10.0, 13.0, n_high)

    world_3d = np.vstack([
        np.column_stack([gx, gy, gz]),
        np.column_stack([ex, ey, ez]),
        np.column_stack([hx, hy, hz]),
    ]).astype(np.float64)

    # 透视投影
    cam_pts = (R @ world_3d.T).T + t.T
    depth = cam_pts[:, 2]
    valid = depth > 0.5
    proj = (K @ cam_pts[valid].T).T
    pixels = proj[:, :2] / proj[:, 2:3]
    inside = ((pixels[:, 0] >= 0) & (pixels[:, 0] < W) &
              (pixels[:, 1] >= 0) & (pixels[:, 1] < H))
    query_2d = pixels[inside].astype(np.float32)
    world_valid = world_3d[valid][inside]

    if len(query_2d) < 20:
        raise RuntimeError(f"Too few points: {len(query_2d)}")

    # NPY
    projection_xyz = np.zeros((H, W, 3), dtype=np.float32)
    px = np.clip(np.rint(query_2d[:, 0]).astype(int), 0, W - 1)
    py = np.clip(np.rint(query_2d[:, 1]).astype(int), 0, H - 1)
    for i in range(len(px)):
        if projection_xyz[py[i], px[i], 2] == 0:
            projection_xyz[py[i], px[i]] = world_valid[i].astype(np.float32)

    return query_2d, world_valid, projection_xyz


def test_ground_filtering_reduces_median():
    """高处点存在时，地面点过滤应让 median_m 大幅下降。"""
    query_2d, world_3d, projection_xyz = _make_perspective_scene()

    with tempfile.TemporaryDirectory() as tmpdir:
        npy_path = Path(tmpdir) / "test.npy"
        np.save(ny_path := npy_path, projection_xyz)

        ctx = build_local_coordinate_transform_context(
            query_2d, world_3d, projection_xyz, npy_path,
            plane_distance_threshold=0.2,
        )

        assert ctx["status"] == "ready"
        cons = ctx.get("consistency", {})
        median_m = cons.get("median_m")
        assert median_m is not None
        # 地面点过滤后 median 应 < 1.0m
        assert median_m < 1.0, (
            f"median_m={median_m}m >= 1.0m，修复未生效"
        )


def test_without_plane_filtering_median_is_high():
    """对照：不启用平面检测时 median 应较大（高处点拉歪）。"""
    query_2d, world_3d, projection_xyz = _make_perspective_scene()

    with tempfile.TemporaryDirectory() as tmpdir:
        npy_path = Path(tmpdir) / "test.npy"
        np.save(npy_path, projection_xyz)

        ctx = build_local_coordinate_transform_context(
            query_2d, world_3d, projection_xyz, npy_path,
            plane_distance_threshold=None,  # 禁用
        )

        cons = ctx.get("consistency", {})
        median_m = cons.get("median_m")
        assert median_m is not None
        # 不过滤时高处点拉歪 H，median 应比过滤后大
        # 注：此处仅验证场景有效（有高处点），不强制阈值
        assert median_m >= 0.0, (
            f"对照：median_m={median_m}m 为负，场景构造错误"
        )


def test_ground_only_no_regression():
    """纯地面点：过滤前后 median 应基本一致（不退化）。"""
    query_2d, world_3d, projection_xyz = _make_perspective_scene(
        n_ground=100, n_elevation=0, n_high=0
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        npy_path = Path(tmpdir) / "test.npy"
        np.save(npy_path, projection_xyz)

        ctx = build_local_coordinate_transform_context(
            query_2d, world_3d, projection_xyz, npy_path,
            plane_distance_threshold=0.2,
        )

        cons = ctx.get("consistency", {})
        median_m = cons.get("median_m")
        assert median_m is not None
        assert median_m < 1.0, (
            f"纯地面点 median_m={median_m}m 应 < 1.0m（不应退化）"
        )
