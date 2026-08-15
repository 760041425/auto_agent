"""008 P3 基准运行器 — ACE 四路径对比 + D-008-03 路由决策。

四条对比路径（008 spec / 007 路由约定）：
- ``scene3ch``       : 场景 3ch RGB-only（resolve_ace_model 默认，不传法线）
- ``6ch_constant``   : 6ch + 常量 0.5 占位（normal_mode="constant"，007 回退基线）
- ``6ch_midas``      : 6ch + 推理期真法线（normal_mode="dsine"，实际走 MiDaS）
- ``6ch_gradient``   : 6ch + 梯度伪法线对照（debug_normal=True，skew 根因输入）

可注入 ``run_fn(path_spec, image_path) -> raw_result`` 跑通流程；真实运行走 CLI
（scripts/benchmark_008.py），需要 ACE 模型 + MiDaS 权重 + LAS kdtree。
"""
from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

#: 四路径定义。path_id 同时作为聚合/报告与决策的键。
PATH_SPECS: List[Dict[str, Any]] = [
    {"path_id": "scene3ch",     "label": "scene 3ch RGB-only",  "normal_mode": "constant", "debug_normal": False, "force_6ch": False},
    {"path_id": "6ch_constant", "label": "6ch + 常量 0.5 占位", "normal_mode": "constant", "debug_normal": False, "force_6ch": True},
    {"path_id": "6ch_midas",    "label": "6ch + 真法线 (MiDaS)", "normal_mode": "dsine",    "debug_normal": False, "force_6ch": True},
    {"path_id": "6ch_gradient", "label": "6ch + 梯度伪法线(对照)", "normal_mode": "constant", "debug_normal": True,  "force_6ch": True},
]


def _normalize(raw: dict, *, query: str, path_id: str) -> dict:
    """把 ace_with_better_normal 的原始返回压扁为基准逐条记录。"""
    q = raw.get("quality", {}) or {}
    v = (raw.get("validations", {}) or {}).get("las_nearest", {}) or {}
    success = bool(raw.get("success"))
    return {
        "query": query,
        "path_id": path_id,
        "success": success,
        "error": raw.get("error") if not success else None,
        "input_mode": raw.get("input_mode"),
        "normal_source": raw.get("normal_source"),
        "inlier_count": q.get("inlier_count") if success else None,
        "reprojection_error_px": q.get("reprojection_error_px") if success else None,
        "las_verify_rate": v.get("verification_rate") if success else None,
        "mean_distance_m": v.get("mean_distance_m") if success else None,
        "elapsed_s": raw.get("elapsed"),
    }


def run_benchmark(
    queries: List[str],
    *,
    run_fn: Callable[[dict, str], dict],
    run_id: Optional[str] = None,
    output_path: Optional[str] = None,
) -> dict:
    """对查询集跑四路径对比，返回报告 dict；可选落 JSON。

    ``run_fn(path_spec, image_path)`` 返回 ACE 原始结果 dict。
    """
    run_id = run_id or f"bench-008-{int(time.time())}"
    results: List[dict] = []
    for path_spec in PATH_SPECS:
        for q in queries:
            try:
                raw = run_fn(path_spec, q)
            except Exception as exc:  # noqa: BLE001 — 单条失败不中断基准
                raw = {"success": False, "error": f"run_fn 异常: {exc}",
                       "input_mode": None, "normal_source": None, "elapsed": None}
            results.append(_normalize(raw, query=Path(q).name, path_id=path_spec["path_id"]))

    aggregate = _aggregate(results)
    report = {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": int(time.time()),
        "path_ids": [p["path_id"] for p in PATH_SPECS],
        "path_labels": {p["path_id"]: p["label"] for p in PATH_SPECS},
        "n_queries": len(queries),
        "queries": [Path(q).name for q in queries],
        "results": results,
        "aggregate": aggregate,
        "decision": decide_routing(aggregate),
    }
    report["decision"]["run_id"] = run_id

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report["output_path"] = str(out)
    return report


def _aggregate(results: List[dict]) -> dict:
    """按 path_id 聚合指标。"""
    by_path: Dict[str, list] = {p["path_id"]: [] for p in PATH_SPECS}
    for r in results:
        by_path[r["path_id"]].append(r)

    agg: dict = {}
    for pid, rows in by_path.items():
        n_total = len(rows)
        ok = [r for r in rows if r["success"]]
        n_success = len(ok)
        success_rate = n_success / n_total if n_total else 0.0

        def _mean(key):
            vals = [r[key] for r in ok if r.get(key) is not None]
            return float(sum(vals) / len(vals)) if vals else None

        agg[pid] = {
            "n_total": n_total,
            "n_success": n_success,
            "success_rate": float(success_rate),
            "mean_distance_m": _mean("mean_distance_m"),
            "mean_reprojection_error_px": _mean("reprojection_error_px"),
            "mean_las_verify_rate": _mean("las_verify_rate"),
            "mean_inlier_count": _mean("inlier_count"),
        }
    return agg


def decide_routing(aggregate: dict) -> dict:
    """D-008-03 数据决策路由。

    切换条件（全部满足才切）：
    1. ``6ch_midas`` 成功率 ≥50%（有统计意义）；
    2. ``6ch_midas`` 的 mean_distance_m 相对 baseline（scene3ch / 6ch_constant
       中更优者）提升 ≥30%，**或** 绝对值 ≤0.5m。

    返回 ``{switch, winner, baseline_id, challenger_id, reasons}``。
    """
    midas = aggregate.get("6ch_midas", {})
    reasons: List[str] = []

    baseline_candidates = ["scene3ch", "6ch_constant"]
    baseline_id = None
    baseline_dist: Optional[float] = None
    for bid in baseline_candidates:
        b = aggregate.get(bid, {})
        d = b.get("mean_distance_m")
        if d is not None and (baseline_dist is None or d < baseline_dist):
            baseline_dist = d
            baseline_id = bid

    if baseline_id is None or baseline_dist is None:
        return _decision(False, winner=baseline_id or "scene3ch", baseline_id=baseline_id,
                         reasons=["baseline 无可用 LAS 距离，维持 007 现状"])

    m_dist = midas.get("mean_distance_m")
    m_rate = midas.get("success_rate", 0.0)
    if m_dist is None:
        return _decision(False, winner=baseline_id, baseline_id=baseline_id,
                         reasons=["6ch_midas 无可用距离，维持 007 现状"])
    if m_rate < 0.5:
        reasons.append(f"6ch_midas 成功率 {m_rate:.0%} < 50%，统计不足")
        return _decision(False, winner=baseline_id, baseline_id=baseline_id, reasons=reasons)

    # 判定（D-008-03）：
    # - 相对门限：相对 baseline 提升 ≥30% → 切；
    # - 绝对门限：mean_distance ≤0.5m 本身为强证据，但为避免在 baseline 已接近 0.5m
    #   时因测量噪声误切（RISK-008-05），额外要求相对改善 ≥10%。
    rel_improve = (baseline_dist - m_dist) / baseline_dist if baseline_dist > 0 else 0.0
    rel_ok = rel_improve >= 0.30
    abs_ok = m_dist <= 0.5 and rel_improve >= 0.10

    if rel_ok:
        reasons.append(f"6ch_midas 相对最优 baseline({baseline_id} {baseline_dist:.3f}m) "
                       f"提升 {rel_improve:.0%} ≥ 30%")
    if abs_ok and not rel_ok:
        reasons.append(f"6ch_midas mean_distance {m_dist:.3f}m ≤ 0.5m 且相对改善 {rel_improve:.0%} ≥ 10%")

    switch = abs_ok or rel_ok
    if not switch:
        reasons.append(f"6ch_midas({m_dist:.3f}m) 未达 ≤0.5m@10% 且相对 baseline 提升 {rel_improve:.0%} < 30%")
    return _decision(switch, winner="6ch_midas" if switch else baseline_id,
                     baseline_id=baseline_id, reasons=reasons,
                     challenger_dist=m_dist, baseline_dist=baseline_dist,
                     relative_improvement=float(rel_improve))


def _decision(switch, *, winner, baseline_id, reasons, **extra):
    return {"switch": switch, "winner": winner, "baseline_id": baseline_id,
            "challenger_id": "6ch_midas", "reasons": reasons, **extra}


def _real_run_fn(path_spec: dict, image_path: str, *, fov_deg: float = 75.0) -> dict:
    """真实 ACE 运行：按 path_spec 选择路径，调用 ace_with_better_normal。"""
    from services.localizer import enhanced_ace

    # scene3ch 走 resolve_ace_model 默认；其余强制 6ch 回退路由
    if path_spec.get("force_6ch"):
        scene_model_path = "/__force_6ch__/nonexistent.pth"  # 不存在 → 回退 6ch
    else:
        scene_model_path = None

    return enhanced_ace.ace_with_better_normal(
        image_path,
        fov_deg=fov_deg,
        normal_mode=path_spec.get("normal_mode", "constant"),
        debug_normal=bool(path_spec.get("debug_normal", False)),
        _scene_model_path=scene_model_path,
    )
