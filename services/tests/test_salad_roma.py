import cv2
import numpy as np

from services.localizer import _render_point_cloud_splat, _estimate_surface_normals
from services.localizer.salad_roma import _is_pose_better


def test_pose_selector_prefers_higher_inlier_count_and_lower_error():
    """TL-002-06: 位姿优选以内点数优先、误差次优。"""
    assert _is_pose_better(12, 3.5, 8, 4.2)
    assert _is_pose_better(8, 2.0, 8, 2.2)
    assert not _is_pose_better(8, 3.0, 10, 2.2)


def test_render_point_cloud_splat_produces_soft_image():
    points = np.array([[0.0, 0.0, 2.0], [0.2, 0.0, 3.0]], dtype=np.float64)
    colors = np.array([[255, 0, 0], [0, 255, 0]], dtype=np.uint8)
    rvec = np.array([0.0, 0.0, 0.0], dtype=np.float64)
    tvec = np.array([0.0, 0.0, 4.0], dtype=np.float64)
    camera = np.array([[64.0, 0.0, 32.0], [0.0, 64.0, 32.0], [0.0, 0.0, 1.0]], dtype=np.float64)

    img = _render_point_cloud_splat(points, colors, camera, 64, 64, rvec=rvec, tvec=tvec, radius=1.5)

    assert img.shape == (64, 64, 3)
    assert np.count_nonzero(img) > 0
    assert img.dtype == np.uint8


def test_estimate_surface_normals_returns_valid_vectors():
    """TL-002-07: 表面法线输出稳定形状和单位向量。"""
    points = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float64)
    normals = _estimate_surface_normals(points)

    assert normals.shape == (3, 3)
    assert np.allclose(np.linalg.norm(normals, axis=1), 1.0, atol=1e-6)
