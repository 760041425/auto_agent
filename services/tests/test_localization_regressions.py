from __future__ import annotations

import numpy as np
import pytest

from services.localizer.verify_projection import (
    build_local_coordinate_transform_context,
    build_projection_xyz_map,
    evaluate_local_coordinate_consistency,
    load_published_tile_images,
    query_local_coordinate_transform,
)


def test_diagnostics_only_load_current_accepted_map_tiles(tmp_path):
    """TL-002-13: 诊断脚本不得按文件名扫描历史多 pitch/水平实验图。"""
    import json

    accepted = tmp_path / "ground_p-15.png"
    rejected = tmp_path / "horizontal_p+0.png"
    accepted.write_bytes(b"published")
    rejected.write_bytes(b"historical")
    index_path = tmp_path / "tile_index.json"
    index_path.write_text(json.dumps([
        {"image_path": str(accepted), "accepted": True},
        {"image_path": str(rejected), "accepted": False},
        {"image_path": str(tmp_path / "missing.png"), "accepted": True},
    ]))

    assert load_published_tile_images(index_path) == [str(accepted)]


def test_v2_final_artifacts_include_query_projection_and_comparison(monkeypatch, tmp_path):
    """TL-003-19: 最终位姿必须产生前端可展示的三类视觉 artifact。"""
    from services.localizer import salad_roma_v2

    query = np.full((16, 16, 3), 64, dtype=np.uint8)
    projection = np.full((16, 16, 3), 192, dtype=np.uint8)

    def fake_render(*args, **kwargs):
        output_dir = args[7]
        name = args[8]
        import cv2

        cv2.imwrite(str(output_dir / name), projection)
        return projection

    monkeypatch.setattr(salad_roma_v2, "_render_projection_local", fake_render)

    artifacts = salad_roma_v2._write_final_artifacts(
        query,
        np.zeros((1, 3)),
        np.zeros((1, 3)),
        np.zeros((3, 1)),
        np.zeros((3, 1)),
        np.eye(3),
        tmp_path,
        "salad_v2_loftr",
    )

    assert set(artifacts) == {"query_image", "reprojection_image", "comparison_image"}
    assert all((tmp_path / path.split("/")[-1]).stat().st_size > 0 for path in artifacts.values())


def test_matcher_missing_tile_path_returns_none_instead_of_name_error():
    """TL-003-06/TL-003-16: tile 路径回退不得引用未导入的 Path。"""
    from services.matcher import _match_on_tile

    result = _match_on_tile(
        q_kp=[],
        q_des=None,
        q_w=10,
        q_h=10,
        tile_info={"image_path": "missing.png", "coord_map_path": "missing.json"},
        task_id=1,
    )

    assert result is None


def test_projection_consistency_does_not_claim_absolute_accuracy_without_ground_truth():
    """TL-003-08/TL-003-22: 同源 NPY 自比较不得输出可用的米制误差。"""
    from services.localizer.verify_projection import verify_projection_local

    image = np.zeros((10, 10, 3), dtype=np.uint8)
    coord_map = np.zeros((10, 10, 3), dtype=np.float32)
    yy, xx = np.mgrid[:10, :10]
    coord_map[..., 0] = xx
    coord_map[..., 1] = yy
    pts = np.array([[1, 1], [8, 1], [8, 8], [1, 8]], dtype=np.float32)

    result = verify_projection_local(image, image, coord_map, pts, pts)

    assert result["metric_type"] == "projection_consistency"
    assert result["absolute_accuracy"] == {
        "status": "not_available",
        "reason": "independent ground truth not provided",
    }
    assert result["status"] == "not_available"
    assert result["reason"] == "same_source_npy_is_not_independent_validation"
    assert result["median_m"] is None
    assert result["max_m"] is None
    assert result["homography_fit"]["status"] == "available"
    assert result["homography_fit"]["n_inliers"] == 4


def test_homography_fit_diagnostic_detects_perturbed_correspondence():
    """TL-003-09/TL-003-22: 同源数据只保留像素拟合诊断，不伪造米制精度。"""
    from services.localizer.verify_projection import verify_projection_local

    image = np.zeros((12, 12, 3), dtype=np.uint8)
    coord_map = np.zeros((12, 12, 3), dtype=np.float32)
    yy, xx = np.mgrid[:12, :12]
    coord_map[..., 0] = xx
    coord_map[..., 1] = yy
    pts_query = np.array([[1, 1], [10, 1], [10, 10], [1, 10], [6, 6]], dtype=np.float32)
    pts_tile = pts_query.copy()
    pts_tile[-1] = [7, 6]

    result = verify_projection_local(
        image,
        image,
        coord_map,
        pts_query,
        pts_tile,
        reproj_thresh=2.0,
    )

    assert result["status"] == "not_available"
    assert result["max_m"] is None
    assert result["homography_fit"]["all_match_max_residual_px"] > 0


def test_local_projection_xyz_map_uses_final_pose_and_nearest_depth():
    """TL-003-25: 最终位姿必须在查询图像素空间生成本地 XYZ NPY。"""
    points = np.array([[0.0, 0.0, 5.0], [0.0, 0.0, 10.0]])
    camera_matrix = np.array([[10.0, 0.0, 5.0], [0.0, 10.0, 5.0], [0.0, 0.0, 1.0]])

    xyz_map = build_projection_xyz_map(
        points,
        np.zeros((3, 1)),
        np.zeros((3, 1)),
        camera_matrix,
        width=11,
        height=11,
        splat_radius=0,
    )

    assert xyz_map.shape == (11, 11, 3)
    assert xyz_map[5, 5].tolist() == [0.0, 0.0, 5.0]


def test_local_coordinate_context_and_query_return_h_xyz_npy_xyz(tmp_path):
    """TL-003-25: 本地迁移能力保留 H→SLAM 与 NPY 两套 XYZ 及差值。"""
    query_points = np.array([[0, 0], [9, 0], [9, 9], [0, 9]], dtype=np.float32)
    world_points = np.array(
        [[10, 20, 3], [19, 20, 3], [19, 29, 3], [10, 29, 3]],
        dtype=np.float32,
    )
    xyz_map = np.zeros((10, 10, 3), dtype=np.float32)
    xyz_map[5, 5] = [15, 25, 3]
    output = tmp_path / "projection_xyz.npy"

    context = build_local_coordinate_transform_context(
        query_points, world_points, xyz_map, output
    )
    result = query_local_coordinate_transform(context, u=5 / 9, v=5 / 9)

    assert context["status"] == "ready"
    assert output.exists()
    assert result["status"] == "available"
    assert result["validation_type"] == "local_coordinate_crosscheck"
    assert result["pixel_to_slam"] == {"slam_x": 15.0, "slam_y": 25.0, "slam_z": 0.0}
    assert result["npy_point"] == {"x": 15.0, "y": 25.0, "z": 3.0}
    assert result["difference_m"] == 3.0
    assert result["absolute_accuracy"] is False


def test_local_coordinate_query_rejects_zero_npy_pixel(tmp_path):
    """TL-003-25: 投影 NPY 无效像素不得伪造为 0 米测量。"""
    npy_path = tmp_path / "empty.npy"
    np.save(npy_path, np.zeros((2, 2, 3), dtype=np.float32))
    context = {
        "status": "ready",
        "homography": np.eye(3).tolist(),
        "projection_npy": str(npy_path),
        "width": 2,
        "height": 2,
    }

    result = query_local_coordinate_transform(context, u=0.5, v=0.5)

    assert result["status"] == "not_available"
    assert result["difference_m"] is None
    assert result["reason"] == "projection_npy_pixel_invalid"


def test_coordinate_consistency_median_is_the_final_pass_fail_standard(tmp_path):
    """TL-003-28: 可信判定使用多点三维坐标差中位数，不使用内点数或相似度。"""
    npy_path = tmp_path / "projection.npy"
    xyz = np.zeros((4, 4, 3), dtype=np.float32)
    for y in range(4):
        for x in range(4):
            # XY 平面内 X 方向偏移 1.0m，Z=0（贴地）
            xyz[y, x] = [x + 1.0, y, 0.0]
    np.save(npy_path, xyz)
    context = {
        "status": "ready",
        "homography": np.eye(3).tolist(),
        "projection_npy": str(npy_path),
        "width": 4,
        "height": 4,
    }

    passed = evaluate_local_coordinate_consistency(context, threshold_m=2.0)
    failed = evaluate_local_coordinate_consistency(context, threshold_m=0.5)
    equal_to_threshold = evaluate_local_coordinate_consistency(context, threshold_m=1.0)
    defaulted = evaluate_local_coordinate_consistency(context)

    assert passed["status"] == "available"
    assert passed["sample_count"] == 16
    assert passed["median_m"] == 1.0
    assert passed["passed"] is True
    assert failed["passed"] is False
    assert equal_to_threshold["passed"] is False
    assert defaulted["threshold_m"] == 0.3
    assert defaulted["passed"] is False
    assert failed["decision_metric"] == "median_3d_difference_m"


def test_coordinate_consistency_incorporates_z_dimension(tmp_path):
    """TL-003-28 (Z 维度修复): 一致性判定必须包含 Z 分量，不能只看 XY 平面。

    单应矩阵 H 把像素映射到 SLAM 地面平面（Z=0），NPY 包含真实高度。
    当 NPY 的 Z 偏离 0 时，三维距离应增大，从而正确暴露高度异常。
    """
    npy_path = tmp_path / "projection_z.npy"
    xyz = np.zeros((4, 4, 3), dtype=np.float32)
    for y in range(4):
        for x in range(4):
            # XY 完全对齐（H→SLAM XY == NPY XY），但 Z 偏离 2.0m
            xyz[y, x] = [x, y, 2.0]
    np.save(npy_path, xyz)
    context = {
        "status": "ready",
        "homography": np.eye(3).tolist(),
        "projection_npy": str(npy_path),
        "width": 4,
        "height": 4,
    }

    result = evaluate_local_coordinate_consistency(context, threshold_m=3.0)

    assert result["status"] == "available"
    assert result["sample_count"] == 16
    # 三维距离 = sqrt(0^2 + 0^2 + 2.0^2) = 2.0m（Z 分量被计入）
    assert result["median_m"] == 2.0
    assert result["passed"] is True

    # 门槛低于 Z 偏移时应失败 — 证明 Z 参与了判定
    failed = evaluate_local_coordinate_consistency(context, threshold_m=1.0)
    assert failed["median_m"] == 2.0
    assert failed["passed"] is False

    # XY 偏移 + Z 偏移合成三维欧氏距离
    xyz_mixed = np.zeros((4, 4, 3), dtype=np.float32)
    for y in range(4):
        for x in range(4):
            xyz_mixed[y, x] = [x + 1.0, y, 2.0]
    np.save(npy_path, xyz_mixed)
    mixed = evaluate_local_coordinate_consistency(context, threshold_m=3.0)
    # sqrt(1.0^2 + 2.0^2) = sqrt(5) ≈ 2.236
    assert mixed["median_m"] == pytest.approx(2.236, abs=1e-3)
    assert mixed["decision_metric"] == "median_3d_difference_m"


def test_pose_error_separates_translation_rotation_and_reprojection_metrics():
    """TL-003-10: 独立真值误差不能与像素重投影误差混为一谈。"""
    from services.localizer.evaluation import compute_pose_error

    result = compute_pose_error(
        {
            "translation": [3.0, 4.0, 0.0],
            "rotation_vector": [0.0, 0.0, np.pi / 2],
            "reprojection_error": 0.25,
        },
        {
            "translation": [0.0, 0.0, 0.0],
            "rotation_vector": [0.0, 0.0, 0.0],
        },
    )

    assert result == {
        "status": "available",
        "translation_error_m": 5.0,
        "rotation_error_deg": 90.0,
    }
