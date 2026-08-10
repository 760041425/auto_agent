from __future__ import annotations

from services.localizer.contracts import normalize_localization_result


def test_ace_raw_without_coordinate_transform_outputs_not_available_consistency():
    """TL-006-01: normalize_localization_result 处理无 coordinate_transform 的 ACE raw。"""
    raw = {
        "success": True,
        "reliable": True,
        "pose": {
            "translation": [-13.2, -12.3, 25.7],
            "quaternion": [0, 0, 0, 1],
        },
        "quality": {"match_count": 8, "inlier_count": 8},
        "validations": {
            "las_nearest": {"total": 10, "verified": 6, "verification_rate": 0.6},
        },
    }

    normalized = normalize_localization_result(
        "train_ace", raw, min_inliers=12, feature_method="ace"
    )

    coordinate_transform = normalized["coordinate_transform"]
    assert coordinate_transform["status"] == "not_available"
    assert coordinate_transform["reason"]
    assert coordinate_transform["consistency"]["status"] == "not_available"
    assert normalized["inliers"] == 8
    assert normalized["total_3d_points"] == 8