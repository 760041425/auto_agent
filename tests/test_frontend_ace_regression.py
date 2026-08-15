"""TL-006-07 回归：SALAD 系有坐标差判据（available+passed）时徽章与判定卡行为不变。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_frontend_ace_reliability import _resolve_localize_badge, _render_coordinate_decision  # noqa: E402


SALAD_RESULT = {
    "success": True,
    "reliable": True,
    "coordinate_transform": {
        "status": "ready",
        "n_inliers": 200,
        "n_matches": 220,
        "consistency": {
            "status": "available",
            "passed": True,
            "median_m": 0.123,
            "p95_m": 0.3,
            "sample_count": 10,
            "threshold_m": 0.3,
        },
    },
}


def test_salad_consistency_passed_badge_still_ok():
    assert _resolve_localize_badge(SALAD_RESULT) == "✓ 可信"


def test_salad_decision_card_shows_median():
    html = _render_coordinate_decision(SALAD_RESULT)
    assert "中位坐标差" in html
    assert "通过 / 可信" in html
    assert "无法判定" not in html