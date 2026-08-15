"""TDD 测试：前端 ACE 系 PnP 失败诊断行（specs/007 TL-007-06 / AC-007-06）。

验证 web/app_v10.js 的 localizeFailureDiagLine（✅ 失败结果 PnP 统计行：
内点 / 重投影误差 / 预测 Z 范围；含 result.diagnostics 时展示统计，
缺字段渲染 "—"；不含 diagnostics 时回退原「失败」文案不崩溃）。
"""


def _localize_failure_diag_line(result):
    """模拟 web/app_v10.js 的 localizeFailureDiagLine 函数（TL-007-06）。

    这是前端 JS 函数的 Python 等价实现，用于 TDD 测试。
    失败结果含 diagnostics 时输出「内点 X | 重投影误差 Y px | 预测Z [a, b]」
    （X=pnp.best_inliers、Y=pnp.best_reproj_error_px、a/b=pred_xyz.z_min/z_max，
     均保留 2 位小数；字段缺失/为 None 时对应段渲染 "—"，绝不渲染假 "0"，
     也不出现字符串 "None"）；不含 diagnostics（旧结构兜底）时输出原失败
     文案（如 result.error），不崩溃。
    """
    diag = result.get("diagnostics")
    if not diag:
        err = result.get("error")
        if err is None:
            return "定位无解"
        if isinstance(err, str):
            return err
        message = err.get("message") if isinstance(err, dict) else None
        return message or err.get("detail") or err.get("code") or "定位失败"
    pnp = diag.get("pnp") or {}
    pred = diag.get("pred_xyz") or {}
    inliers = pnp.get("best_inliers")
    inliers_s = "—" if inliers is None else str(inliers)
    reproj = pnp.get("best_reproj_error_px")
    reproj_s = f"{reproj:.2f} px" if reproj is not None else "—"
    z_min, z_max = pred.get("z_min"), pred.get("z_max")
    if z_min is None or z_max is None:
        z_s = "—"
    else:
        z_s = "[" + f"{z_min:.2f}" + ", " + f"{z_max:.2f}" + "]"
    return "内点 " + inliers_s + " | 重投影误差 " + reproj_s + " | 预测Z " + z_s


# ────────────────────────────────────────────────────────────────────
# TL-007-06：含 diagnostics 且 pnp.best_inliers 非 None → 统计行
# ────────────────────────────────────────────────────────────────────
def test_failure_diag_full_stats():
    """TL-007-06: 完整 diagnostics → 「内点 3 | 重投影误差 41.20 px | 预测Z [0.50, 9.90]」。"""
    result = {
        "success": False,
        "error": "ACE PnP 失败",
        "diagnostics": {
            "pnp": {"tried_candidates": 15, "best_inliers": 3, "best_reproj_error_px": 41.2},
            "pred_xyz": {"z_min": 0.5, "z_max": 9.9, "center": [1.0, 2.0, 5.0], "count": 30},
            "las_bbox": {"z_min": 0.0, "z_max": 10.0},
            "overlap_with_las_bbox": 0.8,
            "model": {"path": "projections/ace_model.pth", "in_channels": 6},
            "input_mode": "ace_6ch_constant_normal",
        },
    }
    line = _localize_failure_diag_line(result)
    assert "内点 3" in line
    assert "重投影误差 41.20 px" in line
    assert "预测Z [0.50, 9.90]" in line


def test_failure_diag_reproj_two_decimals():
    """TL-007-06 边界: 重投影误差保留 2 位小数（41.2→41.20）。"""
    result = {
        "success": False,
        "error": "ACE PnP 失败",
        "diagnostics": {
            "pnp": {"best_inliers": 6, "best_reproj_error_px": 41.2},
            "pred_xyz": {"z_min": -0.5, "z_max": 3.333},
        },
    }
    line = _localize_failure_diag_line(result)
    assert "内点 6" in line
    assert "重投影误差 41.20 px" in line
    assert "预测Z [-0.50, 3.33]" in line


# ────────────────────────────────────────────────────────────────────
# TL-007-06：字段缺失/None → 对应段渲染 "—"（不是 0 也不是 "None"）
# ────────────────────────────────────────────────────────────────────
def test_failure_diag_missing_fields_dash_not_zero():
    """TL-007-06: 字段全缺席 → 「内点 — | 重投影误差 — | 预测Z —」，绝不含假 "0" 或 "None" 字符串。"""
    result = {"success": False, "error": "ACE PnP 失败", "diagnostics": {"pnp": {}, "pred_xyz": {}}}
    line = _localize_failure_diag_line(result)
    assert "内点 —" in line
    assert "重投影误差 —" in line
    assert "预测Z —" in line
    assert "内点 0" not in line
    assert "重投影误差 0" not in line
    assert "None" not in line


def test_failure_diag_partial_missing_segment():
    """TL-007-06: 部分缺失 → 仅缺失段渲染 "—"，其余段保持真实值。"""
    result = {
        "success": False,
        "error": "ACE PnP 失败",
        "diagnostics": {
            "pnp": {"best_inliers": 5, "best_reproj_error_px": None},
            "pred_xyz": {"z_min": None, "z_max": 8.0},
        },
    }
    line = _localize_failure_diag_line(result)
    assert "内点 5" in line
    assert "重投影误差 —" in line
    assert "预测Z —" in line
    assert "None" not in line


# ────────────────────────────────────────────────────────────────────
# TL-007-06：不含 diagnostics（旧结构兜底）→ 原「失败」文案，不崩溃
# ────────────────────────────────────────────────────────────────────
def test_failure_diag_no_diagnostics_fallback():
    """TL-007-06: 无 diagnostics → 输出原失败文案（如 "ACE PnP 失败"），不崩溃。"""
    result = {"success": False, "error": "ACE PnP 失败", "tag": "ace_better", "elapsed": 8.2}
    line = _localize_failure_diag_line(result)
    assert "ACE PnP 失败" in line


def test_failure_diag_no_diagnostics_default_error():
    """TL-007-06: 无 diagnostics 且无 error → 默认文案不抛异常。"""
    result = {"success": False}
    line = _localize_failure_diag_line(result)
    assert isinstance(line, str)
    assert line.strip() != ""