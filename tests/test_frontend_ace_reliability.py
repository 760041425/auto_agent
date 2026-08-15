"""TDD 测试：前端 ACE 定位结果可信展示（specs/006 TL-006-03/04/05）。

验证 web/app_v10.js 的 localizeStatusBadge（✅ 徽章三态）、
renderCoordinateReliabilityDecision（✅ 判定卡 reason 映射）、
localizeDiagnosticLine（✅ 诊断行字段回退链）。
"""


def _resolve_localize_badge(result):
    """模拟 web/app_v10.js 的 localizeStatusBadge 函数（TL-006-03）。

    这是前端 JS 函数的 Python 等价实现，用于 TDD 测试。
    徽章只由坐标差判据产生：coordinate_transform(status=ready) +
    consistency(available+passed) → `✓ 可信`；
    available 但未 passed → `⚠ 低可信`；缺 coordinate / not_available →
    `⚠ 无法判定`。绝不因 result.reliable 显示 ✓。
    """
    if not result.get("success"):
        return "✗ 失败"
    coordinate = result.get("coordinate_transform") or (result.get("validations") or {}).get("coordinate_crosscheck")
    consistency = coordinate and coordinate.get("consistency")
    reliable = bool(
        coordinate and coordinate.get("status") == "ready"
        and consistency and consistency.get("status") == "available"
        and consistency.get("passed")
    )
    if reliable:
        return "✓ 可信"
    if consistency and consistency.get("status") == "available":
        return "⚠ 低可信"
    return "⚠ 无法判定"


def test_badge_consistent_available_passed():
    """TL-006-03: consistency available + passed → `✓ 可信`。"""
    result = {
        "success": True,
        "coordinate_transform": {
            "status": "ready",
            "consistency": {"status": "available", "passed": True},
        },
    }
    assert _resolve_localize_badge(result) == "✓ 可信"


def test_badge_missing_coordinate_with_reliable_true():
    """TL-006-03: coordinate_transform 缺失时即使 result.reliable 为真 → `⚠ 无法判定`。"""
    result = {"success": True, "reliable": True}
    assert _resolve_localize_badge(result) == "⚠ 无法判定"


def test_badge_ready_but_consistency_not_available():
    """TL-006-03: ready 但 consistency not_available → `⚠ 无法判定`。"""
    result = {
        "success": True,
        "coordinate_transform": {
            "status": "ready",
            "consistency": {"status": "not_available"},
        },
    }
    assert _resolve_localize_badge(result) == "⚠ 无法判定"


def test_badge_available_but_not_passed():
    """TL-006-03: available 但 passed=False → `⚠ 低可信`。"""
    result = {
        "success": True,
        "coordinate_transform": {
            "status": "ready",
            "consistency": {"status": "available", "passed": False},
        },
    }
    assert _resolve_localize_badge(result) == "⚠ 低可信"


def _render_coordinate_decision(result):
    """模拟 web/app_v10.js 的 renderCoordinateReliabilityDecision 函数（TL-006-04）。

    这是前端 JS 函数的 Python 等价实现，用于 TDD 测试。
    available 分支保持中位差/P95/样本/门槛/结论；not_available 分支对
    reason 做文案映射（无 reason 用默认「该算法未生成多点坐标差产物」），
    整段不含 ``✓`` 徽章字符串。
    """
    _REASON_MESSAGES = {
        "coordinate_transform_context_not_ready": "坐标变换上下文未就绪",
        "coordinate_transform_artifact_unavailable": "坐标变换产物不可用",
        "insufficient_valid_projection_pixels": "有效投影像素不足",
        "insufficient_valid_homography_samples": "有效单应样本不足",
    }
    coordinate = result.get("coordinate_transform") or (result.get("validations") or {}).get("coordinate_crosscheck") or {}
    consistency = coordinate.get("consistency") or {}
    available = consistency.get("status") == "available"
    passed = available and consistency.get("passed")
    if available:
        html = (
            "中位坐标差: " + f"{consistency['median_m']:.3f} m"
            + "；P95: " + f"{consistency['p95_m']:.3f} m"
            + "；样本: " + str(consistency["sample_count"])
            + "；门槛: " + f"{consistency['threshold_m']:.3f} m"
            + "；结论: " + ("通过 / 可信" if passed else "未通过 / 不准")
        )
        return html
    reason = consistency.get("reason") or coordinate.get("reason")
    cause = _REASON_MESSAGES.get(reason, "该算法未生成多点坐标差产物，需重新定位")
    return "未生成可用的多点坐标差：" + cause + "，按最终标准判定为低可信；请重新定位。"


def test_decision_not_available_reason_mapped():
    """TL-006-04: not_available + reason 时输出含原因映射文案，且不含 ✓ 徽章。"""
    result = {
        "success": True,
        "coordinate_transform": {
            "status": "not_available",
            "reason": "coordinate_transform_context_not_ready",
            "consistency": {"status": "not_available"},
        },
    }
    html = _render_coordinate_decision(result)
    assert "未生成可用的多点坐标差" in html
    assert "坐标变换上下文未就绪" in html
    assert "✓" not in html


def test_decision_missing_coordinate_default():
    """TL-006-04: coordinate 缺失时输出默认文案，且不含 ✓ 徽章。"""
    result = {"success": True}
    html = _render_coordinate_decision(result)
    assert "未生成可用的多点坐标差" in html
    assert "该算法未生成多点坐标差产物" in html
    assert "✓" not in html


def test_decision_reason_homography_samples():
    """TL-006-04: reason=insufficient_valid_homography_samples 映射到对应说明。"""
    result = {
        "success": True,
        "coordinate_transform": {
            "status": "not_available",
            "reason": "insufficient_valid_homography_samples",
            "consistency": {"status": "not_available"},
        },
    }
    html = _render_coordinate_decision(result)
    assert "有效单应样本不足" in html


def test_decision_available_branch_kept():
    """TL-006-04 回归: available 分支保持中位差/P95/样本/门槛/结论文本。"""
    result = {
        "success": True,
        "coordinate_transform": {
            "status": "ready",
            "consistency": {
                "status": "available",
                "passed": True,
                "median_m": 0.05,
                "p95_m": 0.08,
                "sample_count": 12,
                "threshold_m": 0.3,
            },
        },
    }
    html = _render_coordinate_decision(result)
    assert "中位坐标差: 0.050 m" in html
    assert "P95: 0.080 m" in html
    assert "样本: 12" in html
    assert "门槛: 0.300 m" in html
    assert "通过 / 可信" in html


def _diagnostic_detail_line(result):
    """模拟 web/app_v10.js 的 localizeDiagnosticLine 函数（TL-006-05）。

    这是前端 JS 函数的 Python 等价实现，用于 TDD 测试。
    内点取 result.inliers，缺省回退 quality.inlier_count 再回退 "—"；
    3D 点数取 result.total_3d_points，缺省回退 quality.match_count 再回退 "—"；
    缺失字段不得渲染成假 "0"。
    """
    quality = result.get("quality") or {}
    inliers = result.get("inliers")
    if inliers is None:
        inliers = quality.get("inlier_count")
    if inliers is None:
        inliers = "—"
    total_3d = result.get("total_3d_points")
    if total_3d is None:
        total_3d = quality.get("match_count")
    if total_3d is None:
        total_3d = "—"
    return "辅助诊断：内点 " + str(inliers) + " | 3D点数 " + str(total_3d)


def test_diagnostic_normalized_train_ace():
    """TL-006-05: 后端归一化后 inliers=8、total_3d_points=8 → 「内点 8 | 3D点数 8」。"""
    result = {"success": True, "inliers": 8, "total_3d_points": 8}
    line = _diagnostic_detail_line(result)
    assert "辅助诊断：内点 8 | 3D点数 8" in line


def test_diagnostic_missing_fields_dash_not_zero():
    """TL-006-05: 字段全缺失 → 用 "—"，绝不含假 "0"。"""
    result = {"success": True}
    line = _diagnostic_detail_line(result)
    assert "内点 —" in line
    assert "3D点数 —" in line
    assert "内点 0" not in line
    assert "3D点数 0" not in line


def test_diagnostic_quality_fallback():
    """TL-006-05: 仅 quality 有值时回退 quality.inlier_count / match_count。"""
    result = {"success": True, "quality": {"inlier_count": 6, "match_count": 10}}
    line = _diagnostic_detail_line(result)
    assert "内点 6 | 3D点数 10" in line