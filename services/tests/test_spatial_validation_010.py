"""010 空间感特征轻量验证。"""

import pytest
import numpy as np

from services.localizer.spatial_validation import (
    BenchmarkCoverageError,
    SelfMatchLeakError,
    assess_normal_candidate,
    exclude_query_tile,
    load_dense_map_assets,
    require_leave_one_out,
    select_postprocessing_plan,
    select_leave_one_out_tiles,
    summarize_normal_training_coverage,
    summarize_validation_rows,
)


@pytest.mark.parametrize(
    ("middle_error_deg", "expected_eligible"),
    [(20.0, True), (20.01, False)],
)
def test_normal_candidate_requires_three_tiles_and_at_most_twenty_degree_median(
    middle_error_deg, expected_eligible
):
    """TL-010-12：只有 3 tile 总体无向角中位数不超过 20° 才具备软评分资格。"""
    result = assess_normal_candidate(
        {
            "tile-a": [5.0, 10.0, 15.0],
            "tile-b": [18.0, middle_error_deg, 30.0],
            "tile-c": [35.0, 40.0, 45.0],
        },
        required_tiles=3,
        max_median_deg=20.0,
    )

    assert result["observed_tiles"] == 3
    assert result["sample_count"] == 9
    assert result["median_unoriented_error_deg"] == middle_error_deg
    assert result["eligible_for_soft_scoring"] is expected_eligible


def test_normal_angle_comparison_is_invariant_to_raw_cross_product_scale():
    """TL-010-12：未单位化的 XYZ 叉积法线不能因长度小于 0.5 被丢弃。"""
    from scripts.benchmark_010_moge_normals import _compare_with_map

    predicted = np.array([[[0.0, 0.0, 1.0]]], dtype=np.float32)
    raw_cross_product = np.array([[[0.0, 0.0, 1e-4]]], dtype=np.float64)

    summary, errors = _compare_with_map(
        predicted,
        np.array([[True]]),
        raw_cross_product,
        [1.0, 0.0, 0.0, 0.0],
    )

    assert summary["overlap_pixels"] == 1
    assert errors.tolist() == [0.0]


def test_leave_one_out_rejects_self_match_until_query_tile_is_excluded():
    """TL-010-07：同 key 污染必须失败；显式排除后才能进入 benchmark。"""
    query_key = "view_yaw90_-1.1_0.5_0.5_1_p-15"
    index = {
        query_key: {"rgb": "query"},
        "view_yaw90_-1.3_0.4_0.5_421_p-15": {"rgb": "neighbor"},
    }

    with pytest.raises(SelfMatchLeakError, match=query_key):
        require_leave_one_out(index, query_key)

    filtered = exclude_query_tile(index, query_key)

    require_leave_one_out(filtered, query_key)
    assert query_key not in filtered
    assert set(index) != set(filtered)
    assert query_key in index  # 不修改共享索引


def test_spatial_validation_summary_requires_eight_leave_one_out_and_two_real_queries():
    """TL-010-08：8+2 分组、冷/热耗时与真值语义必须稳定。"""
    entries = [
        {
            "accepted": True,
            "tile": f"tile-{i}",
            "image_path": f"projections/tiles/view_yaw90_tile-{i}_{i}_p-15.png",
            "camera_pose": {
                "position_local_m": {"x": float(i), "y": 0.0, "z": 1.0},
                "quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
            },
        }
        for i in range(10)
    ]
    entries += [dict(entries[0]), {"accepted": False, "tile": "rejected"}]

    selected = select_leave_one_out_tiles(entries, count=8)
    assert len(selected) == 8
    assert len({item["tile"] for item in selected}) == 8

    rows = []
    for i, item in enumerate(selected):
        rows.append(
            {
                "sample_type": "leave_one_out",
                "image": item["image_path"],
                "success": True,
                "quality_passed": True,
                "elapsed_s": 20.0 if i == 0 else 10.0 + i,
                "cold_start": i == 0,
                "self_match_excluded": True,
                "pose_error": {
                    "status": "available",
                    "translation_error_m": 0.01 * (i + 1),
                    "rotation_error_deg": 0.1 * (i + 1),
                },
            }
        )
    rows.extend(
        {
            "sample_type": "real_query",
            "image": f"query-{i}.jpg",
            "success": True,
            "quality_passed": False,
            "elapsed_s": 12.0 + i,
            "cold_start": False,
            "self_match_excluded": None,
            "pose_error": {"status": "unavailable"},
        }
        for i in range(2)
    )

    summary = summarize_validation_rows(rows, required_leave_one_out=8, required_real=2)

    assert summary["leave_one_out"]["n_total"] == 8
    assert summary["leave_one_out"]["ground_truth_n"] == 8
    assert summary["leave_one_out"]["cold_latency_s"] == 20.0
    assert summary["leave_one_out"]["translation_error_mean_m"] == pytest.approx(0.045)
    assert summary["real_query"]["n_total"] == 2
    assert summary["real_query"]["ground_truth_n"] == 0
    assert summary["real_query"]["accuracy_status"] == "diagnostic_only"

    with pytest.raises(BenchmarkCoverageError, match="real_query"):
        summarize_validation_rows(rows[:-1], required_leave_one_out=8, required_real=2)


def test_pose_only_benchmark_skips_heavy_postprocessing_but_default_stays_complete():
    """TL-010-15/16：pose-only 跳过非评分后处理，生产默认保持完整。"""
    default_plan = select_postprocessing_plan(pose_only_benchmark=False)
    pose_only_plan = select_postprocessing_plan(pose_only_benchmark=True)
    assert default_plan == {
        "dense_point_cloud": True,
        "las_verification": True,
        "projection_verification": True,
        "coordinate_transform": True,
        "visual_artifacts": True,
    }
    assert pose_only_plan == {
        "dense_point_cloud": False,
        "las_verification": False,
        "projection_verification": False,
        "coordinate_transform": False,
        "visual_artifacts": False,
    }

    calls = []
    marker = {"points": object(), "colors": object(), "tree": object()}

    def loader():
        calls.append("loaded")
        return marker

    assert load_dense_map_assets(pose_only_plan, loader) is None
    assert calls == []
    assert load_dense_map_assets(default_plan, loader) is marker
    assert calls == ["loaded"]


def test_pose_only_comparison_forwards_a_distinct_seed_to_each_pnp_attempt(monkeypatch):
    """TL-010-16：同种子 A/B 对照必须真正固定每次 PnP RANSAC。"""
    from services.localizer import pose_utils

    observed_seeds = []

    def fake_solve_pnp(*args, **kwargs):
        observed_seeds.append(kwargs.get("ransac_seed"))
        return (
            np.zeros((3, 1), dtype=np.float64),
            np.array([[0.0], [0.0], [5.0]], dtype=np.float64),
            np.arange(4, dtype=np.int32).reshape(-1, 1),
        )

    monkeypatch.setattr(pose_utils, "solve_pnp_ransac", fake_solve_pnp)
    object_pts = np.array(
        [[-1.0, -1.0, 1.0], [1.0, -1.0, 1.0], [1.0, 1.0, 1.0], [-1.0, 1.0, 1.0]],
        dtype=np.float64,
    )
    image_pts = np.array(
        [[20.0, 20.0], [40.0, 20.0], [40.0, 40.0], [20.0, 40.0]],
        dtype=np.float64,
    )

    result = pose_utils.solve_pnp_with_focal_search(
        object_pts,
        image_pts,
        64,
        64,
        coarse_rounds=1,
        fine_rounds=0,
        splits=2,
        min_inliers=4,
        ransac_seed=91,
    )

    assert result["success"] is True
    assert observed_seeds == [91, 92]


def test_normal_coverage_requires_eight_positions_without_reducing_xyz_supervision():
    """TL-010-14：法向覆盖是输入信息量，不得误报为 ACE 监督样本量。"""
    rows = [
        {
            "tile": f"position-{i}",
            "xyz_supervision_pixels": 100,
            "published_normal_on_supervised_pixels": 80,
            "candidate_normal_on_supervised_pixels": 60 + i,
        }
        for i in range(8)
    ]

    summary = summarize_normal_training_coverage(rows, required_tiles=8)

    assert summary["observed_tiles"] == 8
    assert summary["xyz_supervision_pixels"] == 800
    assert summary["supervision_pixel_delta"] == 0
    assert summary["published_normal_coverage_on_supervision"] == pytest.approx(0.8)
    assert summary["candidate_normal_coverage_on_supervision"] == pytest.approx(
        508 / 800
    )
    assert summary["candidate_vs_published_coverage_delta"] == pytest.approx(
        (508 - 640) / 800
    )
    assert summary["interpretation"] == "normal_input_coverage_only"

    rows[-1] = {**rows[-1], "tile": "position-0"}
    with pytest.raises(BenchmarkCoverageError, match="不同 tile"):
        summarize_normal_training_coverage(rows, required_tiles=8)
