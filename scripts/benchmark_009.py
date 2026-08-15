"""009 特征匹配加速对比 CLI — 原算法 vs 加速算法（FAISS / XFeat / fast_mode）。

对比组：
  - salad_roma_v2_loftr（原版 LoFTR）vs salad_v2_loftr_fast（MPS+FAISS+fast_mode）
  - hybrid（原版）vs salad_v2_hybrid_fast（加速版）
  - salad_v2_xfeat（XFeat，若安装）

运行产物（reports/benchmark_009_*.json）不入库。

示例：
    python scripts/benchmark_009.py --queries query_images/ --output reports/benchmark_009.json
    python scripts/benchmark_009.py --queries query_images/ --max-queries 5 --output reports/benchmark_009.json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path

# 项目根目录加入 path（与 render_multi_pitch_tiles.py 一致）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _gather_queries(query_dir: str, max_queries: int | None) -> list[str]:
    """收集查询图（真实图 + tile 渲染图）。"""
    queries = sorted(glob.glob(os.path.join(query_dir, "*.jpg")))
    queries += sorted(glob.glob(os.path.join(query_dir, "*.png")))
    # 也加 tile 渲染图
    tiles = sorted(glob.glob("projections/tiles/*.png"))[:10]
    queries += tiles
    if max_queries is not None:
        queries = queries[:max_queries]
    return queries


def _run_one(algorithm_id: str, image_path: str, output_dir: str) -> dict:
    """运行单个算法，返回 {success, latency_s, inliers, error, algorithm_id, image}。"""
    from services.localizer.registry import DEFAULT_ALGORITHM_REGISTRY, LocalizationInput

    try:
        algo = DEFAULT_ALGORITHM_REGISTRY.get(algorithm_id)
    except KeyError:
        return {"success": False, "error": f"unknown algorithm: {algorithm_id}",
                "algorithm_id": algorithm_id, "image": image_path, "latency_s": 0,
                "inliers": 0, "reprojection_error": None}

    inp = LocalizationInput(
        image_path=image_path,
        output_dir=os.path.join(output_dir, algorithm_id),
        max_iterations=2,
        fov_deg=75.0,
        use_pose_prior=False,
        reproj_error=4.0,
        min_inliers=6,
    )

    t0 = time.time()
    try:
        result = algo.runner(inp)
    except Exception as e:
        return {"success": False, "error": str(e)[:200],
                "algorithm_id": algorithm_id, "image": image_path,
                "latency_s": time.time() - t0, "inliers": 0,
                "reprojection_error": None}
    dt = time.time() - t0

    return {
        "success": bool(result.get("success")),
        "error": result.get("error"),
        "algorithm_id": algorithm_id,
        "image": os.path.basename(image_path),
        "latency_s": round(dt, 3),
        "inliers": result.get("inlier_count", result.get("inliers", 0)) or 0,
        "reprojection_error": result.get("reprojection_error"),
        "strategy": result.get("strategy"),
        "tag": result.get("tag"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="009 特征匹配加速对比基准")
    parser.add_argument("--queries", default="query_images",
                        help="查询图目录（默认 query_images）")
    parser.add_argument("--max-queries", type=int, default=None,
                        help="查询图数量上限（默认全部）")
    parser.add_argument("--algorithms", nargs="+", default=None,
                        help="指定算法 id（默认跑全部对比组）")
    parser.add_argument("--output", default="reports/benchmark_009.json",
                        help="JSON 报告输出路径（默认 reports/benchmark_009.json）")
    parser.add_argument("--output-dir", default="projections/benchmark_009",
                        help="运行产物目录（默认 projections/benchmark_009）")
    args = parser.parse_args()

    # SSL 修复（与 normal_008 一致）
    if not os.environ.get("SSL_CERT_FILE") and not os.environ.get("REQUESTS_CA_BUNDLE"):
        _sc = "/etc/ssl/cert.pem"
        if os.path.exists(_sc):
            os.environ["SSL_CERT_FILE"] = _sc

    queries = _gather_queries(args.queries, args.max_queries)
    if not queries:
        print(f"错误：未找到任何查询图（{args.queries}）", file=sys.stderr)
        return 2
    print(f"查询集：{len(queries)} 张")

    # 默认对比组
    if args.algorithms:
        algorithms = args.algorithms
    else:
        algorithms = [
            "salad_roma_v2_loftr",
            "salad_v2_loftr_fast",
            "hybrid",
            "salad_v2_hybrid_fast",
        ]
        # 动态加 XFeat（若可用）
        from services.localizer.salad_roma_v2 import _has_xfeat
        if _has_xfeat():
            algorithms.append("salad_v2_xfeat")
            print("  XFeat 可用，加入对比")
        else:
            print("  XFeat 未安装，跳过")

    # 过滤不可用的算法
    from services.localizer.registry import DEFAULT_ALGORITHM_REGISTRY
    available_ids = set(DEFAULT_ALGORITHM_REGISTRY.ids())
    algorithms = [a for a in algorithms if a in available_ids]
    print(f"对比算法：{algorithms}")

    os.makedirs(args.output_dir, exist_ok=True)

    # 运行
    results = []
    total = len(queries) * len(algorithms)
    done = 0
    for q in queries:
        for aid in algorithms:
            r = _run_one(aid, q, args.output_dir)
            results.append(r)
            done += 1
            status = "✓" if r["success"] else "✗"
            print(f"  [{done}/{total}] {aid} × {r['image']}: {status} {r['latency_s']:.2f}s"
                  + (f" ({r['inliers']} inliers)" if r["success"] else f" ({r.get('error','')[:40]})"))

    # 聚合
    by_algo = {}
    for r in results:
        aid = r["algorithm_id"]
        by_algo.setdefault(aid, []).append(r)

    aggregate = {}
    for aid, items in by_algo.items():
        succ = [x for x in items if x["success"]]
        latencies = [x["latency_s"] for x in items]
        inliers = [x["inliers"] for x in succ]
        errors = [x["reprojection_error"] for x in succ if x["reprojection_error"] is not None]
        aggregate[aid] = {
            "n_total": len(items),
            "n_success": len(succ),
            "success_rate": len(succ) / len(items) if items else 0,
            "latency_avg_s": round(sum(latencies) / len(latencies), 3) if latencies else None,
            "latency_min_s": round(min(latencies), 3) if latencies else None,
            "latency_max_s": round(max(latencies), 3) if latencies else None,
            "inliers_avg": round(sum(inliers) / len(inliers), 1) if inliers else None,
            "reproj_err_avg": round(sum(errors) / len(errors), 2) if errors else None,
        }

    # 决策：fast 是否可升级为默认
    decision = {"switch": False, "reasons": [], "winner": None}
    if "salad_roma_v2_loftr" in aggregate and "salad_v2_loftr_fast" in aggregate:
        orig = aggregate["salad_roma_v2_loftr"]
        fast = aggregate["salad_v2_loftr_fast"]
        # 条件：fast 成功率 ≥ orig 且延迟 ≤ orig × 0.7
        if (fast["success_rate"] >= orig["success_rate"]
                and fast["latency_avg_s"] is not None and orig["latency_avg_s"] is not None
                and fast["latency_avg_s"] <= orig["latency_avg_s"] * 0.7):
            decision["switch"] = True
            decision["winner"] = "salad_v2_loftr_fast"
            decision["reasons"].append(
                f"fast 成功率 {fast['success_rate']:.0%} ≥ orig {orig['success_rate']:.0%}")
            decision["reasons"].append(
                f"fast 延迟 {fast['latency_avg_s']:.2f}s ≤ orig {orig['latency_avg_s']:.2f}s × 0.7")
        else:
            if fast["success_rate"] < orig["success_rate"]:
                decision["reasons"].append(
                    f"fast 成功率 {fast['success_rate']:.0%} < orig {orig['success_rate']:.0%}，不切换")
            if (fast["latency_avg_s"] is not None and orig["latency_avg_s"] is not None
                    and fast["latency_avg_s"] > orig["latency_avg_s"] * 0.7):
                decision["reasons"].append(
                    f"fast 延迟 {fast['latency_avg_s']:.2f}s 未达 orig × 0.7 目标")

    report = {
        "run_id": f"bench-009-{time.strftime('%Y%m%d-%H%M%S')}",
        "n_queries": len(queries),
        "algorithms": algorithms,
        "results": results,
        "aggregate": aggregate,
        "decision": decision,
    }

    # 写报告
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # 控制台摘要
    print(f"\n=== 009 加速对比完成 ===")
    print(f"报告：{out_path.resolve()}\n")
    header = f"{'算法':<28} {'成功':>6} {'成功率':>7} {'延迟avg':>9} {'延迟min':>9} {'延迟max':>9} {'内点':>6}"
    print(header)
    print("-" * len(header))
    for aid, a in aggregate.items():
        print(f"{aid:<28} {a['n_success']:>6} {a['success_rate']:>6.0%} "
              f"{a['latency_avg_s'] or 0:>8.2f}s {a['latency_min_s'] or 0:>8.2f}s "
              f"{a['latency_max_s'] or 0:>8.2f}s {a['inliers_avg'] or 0:>6.1f}")

    if decision["switch"]:
        print(f"\n✅ 决策：可升级默认 → {decision['winner']}")
    else:
        print(f"\n❌ 决策：维持现状")
    for r in decision["reasons"]:
        print(f"   • {r}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
