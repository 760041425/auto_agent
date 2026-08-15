"""TL-006-06 集成：train_ace 归一化产物 → 接口返回形状 → 前端徽章展示链路。"""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))

from test_frontend_ace_reliability import _resolve_localize_badge  # noqa: E402

from services.localizer.registry import _finalize_ace_result  # noqa: E402


ACE_RAW = {
    "success": True,
    "reliable": True,
    "pose": {"translation": [-13.2, -12.3, 25.7], "quaternion": [0.0, 0.0, 0.0, 1.0]},
    "quality": {"match_count": 8, "inlier_count": 8},
    "validations": {"las_nearest": {"total": 10, "verified": 6, "verification_rate": 0.6}},
}


@pytest.mark.integration
def test_train_ace_finalized_result_api_shape_and_badge():
    finalized = _finalize_ace_result(
        dict(ACE_RAW), algorithm_id="train_ace", min_inliers=12, elapsed_s=789.6, feature_method="ace"
    )
    task_result_json = {"results": [finalized], "total": 1}
    r0 = task_result_json["results"][0]
    assert r0["coordinate_transform"]["status"] == "not_available"
    assert r0["inliers"] == 8
    assert r0["total_3d_points"] == 8
    assert isinstance(r0["timings"]["total_s"], float)
    assert _resolve_localize_badge(r0) == "⚠ 无法判定"