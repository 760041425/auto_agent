"""TL-006-02: _run_train_ace 完成分支覆写必须与 _append_result 归一化等价。"""

from __future__ import annotations

import pytest

from api.routes.localize import _append_result
from services.localizer.registry import _finalize_ace_result

ACE_RAW_RESULT = {
    "success": True,
    "tag": "ace_rgb",
    "reliable": False,
    "pose": {
        "quaternion": [0.0, 0.0, 0.0, 1.0],
        "translation": [-13.2, -12.3, 25.7],
        "rotation_vector": [0.0, 0.0, 0.0],
    },
    "quality": {
        "match_count": 8,
        "inlier_count": 8,
        "reprojection_error_px": 2.5,
        "score": 0.8,
    },
    "validations": {
        "las_nearest": {"total": 10, "verified": 6, "verification_rate": 0.6},
    },
    "elapsed": 1.5,
    "spatial_config": {"method": "ace_3ch_rgb"},
}


def test_finalize_ace_result_matches_append_result_key_set():
    """TL-006-02: 完成覆写归一化后键集合与 _append_result 产物等价。"""
    reference = []
    _append_result(reference, "train_ace", ACE_RAW_RESULT, min_inliers=12, elapsed_s=1.5)
    expected = reference[0]

    normalized = _finalize_ace_result(
        ACE_RAW_RESULT,
        algorithm_id="train_ace",
        min_inliers=12,
        elapsed_s=1.5,
        feature_method="ace",
    )

    assert set(normalized) == set(expected)
    assert normalized["algorithm_id"] == "train_ace"
    assert normalized["success"] is True
    assert normalized["pose"] == ACE_RAW_RESULT["pose"]


def test_finalize_ace_result_derives_localization_summary_fields():
    """TL-006-02: inliers/total_3d_points/coordinate_transform/timings 补齐。"""
    normalized = _finalize_ace_result(
        ACE_RAW_RESULT,
        algorithm_id="train_ace",
        min_inliers=12,
        elapsed_s=1.5,
        feature_method="ace",
    )

    assert normalized["inliers"] == 8
    assert normalized["inliers"] == normalized["quality"]["inlier_count"]
    assert normalized["total_3d_points"] == 8
    assert normalized["total_3d_points"] == normalized["quality"]["match_count"]
    assert normalized["coordinate_transform"]["status"] == "not_available"
    assert isinstance(normalized["timings"]["total_s"], (int, float))