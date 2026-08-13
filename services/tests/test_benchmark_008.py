"""008 P3 基准运行器 + 路由决策（TL-008-04 / TL-008-05）。

四路径对比：scene3ch / 6ch_constant / 6ch_midas / 6ch_gradient。
可注入 ``run_fn`` 跑通流程（不依赖真实 ACE/MIiDaS/LAS），真实运行走 CLI。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import services.localizer.benchmark_008 as b008


# ────────────────────────────────────────────────────────────────────
# 假数据助手
# ────────────────────────────────────────────────────────────────────
def _raw(success: bool, *, path: str, inliers: int = 12, reproj: float = 400.0,
         verify: float = 0.8, dist: float = 1.0, mode: str = "ace_6ch_constant_normal",
         normal_src: str = "constant_fallback", elapsed: float = 2.0):
    """构造一个与 ace_with_better_normal 成功/失败返回同字段的原始结果。"""
    if not success:
        return {"success": False, "error": "ACE PnP 失败", "tag": "ace_better_normal",
                "input_mode": mode, "normal_source": normal_src, "elapsed": elapsed}
    return {"success": True, "tag": "ace_better_normal", "reliable": verify > 0.3,
            "pose": {"translation": [0.0, 0.0, 5.0], "rotation_vector": [0, 0, 0],
                     "quaternion": [1, 0, 0, 0]},
            "quality": {"match_count": inliers, "inlier_count": inliers,
                        "reprojection_error_px": reproj, "score": 1.0},
            "validations": {"las_nearest": {"total": 20, "verified": int(20 * verify),
                                            "verification_rate": verify,
                                            "mean_distance_m": dist, "details": []}},
            "elapsed": elapsed, "input_mode": mode, "normal_source": normal_src}


def _make_run_fn(scenario: dict):
    """scenario: {path_id: success_bool} → 返回注入用的 run_fn。"""
    def _run(path_spec, image_path):
        pid = path_spec["path_id"]
        ok = scenario.get(pid, True)
        # 为不同路径给不同指标，使聚合/路由有意义
        if pid == "6ch_midas" and ok:
            return _raw(True, path=pid, inliers=14, reproj=350.0, verify=0.85, dist=0.4,
                        mode="ace_6ch_constant_normal", normal_src="mi_das")
        if pid == "6ch_gradient" and ok:
            return _raw(True, path=pid, inliers=10, reproj=600.0, verify=0.6, dist=1.1,
                        mode="ace_6ch_constant_normal", normal_src="gradient_debug")
        if pid == "scene3ch" and ok:
            return _raw(True, path=pid, inliers=11, reproj=560.0, verify=0.7, dist=1.8,
                        mode="ace_scene_rgb3ch", normal_src="none_rgb3ch")
        return _raw(ok, path=pid)
    return _run


# ────────────────────────────────────────────────────────────────────
# TL-008-04：基准运行器输出四路径对比表
# ────────────────────────────────────────────────────────────────────
def test_benchmark_runs_all_four_paths_and_reports_metrics(tmp_path):
    """TL-008-04: 运行器对查询集输出四路径对比，指标含 success/verify/dist/reproj/inliers。"""
    queries = [str(tmp_path / f"q{i}.jpg") for i in range(3)]
    run_fn = _make_run_fn({"scene3ch": True, "6ch_constant": True,
                           "6ch_midas": True, "6ch_gradient": True})

    report = b008.run_benchmark(queries, run_fn=run_fn, run_id="test-008")

    # 结构：含四路径 × 3 查询 共 12 条结果
    assert report["run_id"] == "test-008"
    assert set(report["path_ids"]) == {"scene3ch", "6ch_constant", "6ch_midas", "6ch_gradient"}
    assert len(report["results"]) == 12  # 4 paths × 3 queries

    # 逐条结果字段契约
    r0 = report["results"][0]
    for key in ("query", "path_id", "success", "inlier_count", "reprojection_error_px",
                "las_verify_rate", "mean_distance_m", "elapsed_s", "input_mode", "normal_source"):
        assert key in r0, f"结果缺少字段 {key}"

    # 聚合：每条路径一条汇总
    agg = report["aggregate"]
    assert set(agg.keys()) == {"scene3ch", "6ch_constant", "6ch_midas", "6ch_gradient"}
    midas = agg["6ch_midas"]
    assert midas["n_total"] == 3
    assert midas["n_success"] == 3
    assert midas["success_rate"] == 1.0
    assert midas["mean_distance_m"] == pytest.approx(0.4)
    assert midas["mean_reprojection_error_px"] == pytest.approx(350.0)
    assert midas["mean_las_verify_rate"] == pytest.approx(0.85)


def test_benchmark_writes_json_report(tmp_path):
    """TL-008-04: 运行器把报告落盘到 JSON（reports/ 契约）。"""
    queries = [str(tmp_path / "q.jpg")]
    run_fn = _make_run_fn({p["path_id"]: True for p in b008.PATH_SPECS})

    out = tmp_path / "benchmark_008_test.json"
    b008.run_benchmark(queries, run_fn=run_fn, output_path=str(out), run_id="disk-008")

    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["run_id"] == "disk-008"
    assert len(data["results"]) == 4  # 1 query × 4 paths


def test_benchmark_handles_failure_gracefully():
    """TL-008-04: 某路径全部失败时聚合不崩溃，success_rate=0、指标为 None。"""
    queries = [f"/fake/q{i}.jpg" for i in range(2)]
    # 6ch_midas 全失败
    run_fn = _make_run_fn({"6ch_midas": False})

    report = b008.run_benchmark(queries, run_fn=run_fn, run_id="fail-008")
    midas = report["aggregate"]["6ch_midas"]
    assert midas["n_success"] == 0
    assert midas["success_rate"] == 0.0
    assert midas["mean_distance_m"] is None  # 无成功样本 → 无距离


# ────────────────────────────────────────────────────────────────────
# TL-008-05：路由决策 D-008-03
# ────────────────────────────────────────────────────────────────────
def test_decide_routing_switches_when_midas_dominates():
    """TL-008-05: MiDaS 显著胜出（dist ≤0.5m 且优于 baseline ≥30%）→ 切默认路由。"""
    agg = {
        "scene3ch": _agg(dist=1.8, reproj=560, verify=0.7, inliers=11, n=10),
        "6ch_constant": _agg(dist=0.52, reproj=600, verify=0.8, inliers=12, n=10),
        "6ch_midas": _agg(dist=0.40, reproj=350, verify=0.85, inliers=14, n=10),
        "6ch_gradient": _agg(dist=1.1, reproj=620, verify=0.6, inliers=10, n=10),
    }
    decision = b008.decide_routing(agg)
    assert decision["switch"] is True
    assert decision["winner"] == "6ch_midas"
    # 0.40 ≤ 0.5m 触发；相对最优 baseline(0.52) 提升 (0.52-0.40)/0.52 ≈ 23% 但 ≤0.5m 已触发
    assert any(("≤0.5m" in r) or ("0.5" in r) for r in decision["reasons"])


def test_decide_routing_keeps_when_madis_not_better():
    """TL-008-05: MiDaS 未显著胜出 → 维持 007 现状，不切换。"""
    agg = {
        "scene3ch": _agg(dist=1.8, reproj=560, verify=0.7, inliers=11, n=10),
        "6ch_constant": _agg(dist=0.52, reproj=600, verify=0.8, inliers=12, n=10),
        "6ch_midas": _agg(dist=0.50, reproj=590, verify=0.80, inliers=12, n=10),  # 仅持平
        "6ch_gradient": _agg(dist=1.1, reproj=620, verify=0.6, inliers=10, n=10),
    }
    decision = b008.decide_routing(agg)
    assert decision["switch"] is False
    assert decision["winner"] in ("6ch_constant", "scene3ch")


def test_decide_routing_switches_on_30pct_improvement():
    """TL-008-05: MiDaS 未达 ≤0.5m，但相对最优提升 ≥30% → 切换。"""
    agg = {
        "scene3ch": _agg(dist=2.0, reproj=560, verify=0.7, inliers=11, n=10),
        "6ch_constant": _agg(dist=1.0, reproj=600, verify=0.8, inliers=12, n=10),
        "6ch_midas": _agg(dist=0.65, reproj=560, verify=0.82, inliers=13, n=10),  # 1→0.65 = 35%↓
        "6ch_gradient": _agg(dist=1.5, reproj=620, verify=0.6, inliers=10, n=10),
    }
    decision = b008.decide_routing(agg)
    assert decision["switch"] is True
    assert decision["winner"] == "6ch_midas"


def _agg(dist, reproj, verify, inliers, n):
    return {"n_total": n, "n_success": n, "success_rate": 1.0,
            "mean_distance_m": dist, "mean_reprojection_error_px": reproj,
            "mean_las_verify_rate": verify, "mean_inlier_count": inliers}


# ────────────────────────────────────────────────────────────────────
# TL-008-05 回归：不切换时 007 默认路由（scene 3ch 优先）不回归
# ────────────────────────────────────────────────────────────────────
def test_no_switch_keeps_007_default_route(monkeypatch):
    """TL-008-05: 基准未触发切换 → resolve_ace_model 默认仍 scene 3ch 优先（007 行为不回归）。"""
    # 构造 scene 3ch 模型存在的环境
    from services.localizer import enhanced_ace
    from services.localizer.coord_regression import _detect_architecture

    class _FakeEnc:
        def __init__(self, ch):
            self.conv1 = SimpleNamespace(in_channels=ch)

    def _fake_load(path, *a, **k):
        p = str(path)
        if "scene" in p:
            return SimpleNamespace(encoder=_FakeEnc(3))
        return SimpleNamespace(encoder=_FakeEnc(6))

    monkeypatch.setattr(enhanced_ace.os.path, "exists", lambda p: True)
    from services.localizer import coord_regression as cr
    monkeypatch.setattr(cr, "load_coord_regression", _fake_load)

    model, info = enhanced_ace.resolve_ace_model()
    assert info["input_mode"] == "ace_scene_rgb3ch", "默认路由应保持 scene 3ch 优先（007 行为）"
    assert info["normal_source"] == "none_rgb3ch"
