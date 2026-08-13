"""008 P3 基准 CLI — ACE 四路径对比（scene3ch / 6ch_constant / 6ch_midas / 6ch_gradient）。

运行产物（reports/benchmark_008_<ts>.json）不入库。需要：
- ACE 模型（projections/ace_model*.pth）；
- MiDaS 权重（首次 torch.hub 下载缓存到 TORCH_HOME，需外网，见 normal_estimator）；
- LAS kdtree（自动加载 las/default_*.las，~8s）。

示例：
    SSL_CERT_FILE=/etc/ssl/cert.pem python scripts/benchmark_008.py \
        --max-tiles 20 --output reports/benchmark_008.json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path


def _gather_queries(max_tiles: int | None) -> list[str]:
    """查询集：真实 query 图 + tile 渲染图（有 _resolve_pose_from_tile 真值）。"""
    queries = sorted(glob.glob("query_images/*.jpg"))
    tiles = sorted(glob.glob("projections/tiles/*.png"))
    if max_tiles is not None:
        tiles = tiles[:max_tiles]
    return queries + tiles


def _make_real_run_fn(*, fov_deg: float):
    from services.localizer import benchmark_008 as b008

    def _run(path_spec: dict, image_path: str) -> dict:
        return b008._real_run_fn(path_spec, image_path, fov_deg=fov_deg)

    return _run


def main() -> int:
    parser = argparse.ArgumentParser(description="008 ACE 四路径基准")
    parser.add_argument("--max-tiles", type=int, default=20,
                        help="tile 渲染图数量上限（默认 20；0=仅真实查询图）")
    parser.add_argument("--fov", type=float, default=75.0, help="视场角（度，默认 75）")
    parser.add_argument("--output", default="reports/benchmark_008.json",
                        help="JSON 报告输出路径（默认 reports/benchmark_008.json）")
    parser.add_argument("--run-id", default=None, help="自定义 run_id（默认自动生成）")
    args = parser.parse_args()

    # 显式修复 venv certifi（与 normal_estimator 一致），避免 torch.hub SSL 失败。
    if not os.environ.get("SSL_CERT_FILE") and not os.environ.get("REQUESTS_CA_BUNDLE"):
        _sc = "/etc/ssl/cert.pem"
        if os.path.exists(_sc):
            os.environ["SSL_CERT_FILE"] = _sc

    queries = _gather_queries(args.max_tiles if args.max_tiles > 0 else 0)
    if not queries:
        print("错误：未找到任何查询图（query_images/*.jpg / projections/tiles/*.png）", file=sys.stderr)
        return 2
    print(f"查询集：{len(queries)} 张（{len(glob.glob('query_images/*.jpg'))} 真实图 + "
          f"{len(queries) - len(glob.glob('query_images/*.jpg'))} tile）")

    from services.localizer import benchmark_008 as b008

    run_id = args.run_id or f"bench-008-{time.strftime('%Y%m%d-%H%M%S')}"
    run_fn = _make_real_run_fn(fov_deg=args.fov)

    t0 = time.time()
    report = b008.run_benchmark(queries, run_fn=run_fn, run_id=run_id, output_path=args.output)
    dt = time.time() - t0

    # 控制台摘要
    print(f"\n=== 008 基准完成（run_id={run_id}，耗时 {dt:.1f}s）===")
    print(f"报告：{Path(args.output).resolve()}\n")
    agg = report["aggregate"]
    header = f"{'路径':<14} {'成功':>6} {'成功率':>7} {'mean_dist_m':>12} {'reproj_px':>10} {'LAS验证率':>9} {'内点':>6}"
    print(header)
    print("-" * len(header))
    for pid in report["path_ids"]:
        a = agg[pid]
        d_str = f"{a['mean_distance_m']:.3f}" if a['mean_distance_m'] is not None else "-"
        r_str = f"{a['mean_reprojection_error_px']:.1f}" if a['mean_reprojection_error_px'] is not None else "-"
        v_str = f"{a['mean_las_verify_rate']:.2f}" if a['mean_las_verify_rate'] is not None else "-"
        i_str = f"{a['mean_inlier_count']:.1f}" if a['mean_inlier_count'] is not None else "-"
        print(f"{pid:<14} {a['n_success']:>6} {a['success_rate']:>6.0%} {d_str:>12} {r_str:>10} {v_str:>9} {i_str:>6}")

    dec = report["decision"]
    print(f"\n路由决策（D-008-03）：{'✅ 切换默认路由 → ' + dec['winner'] if dec['switch'] else '❌ 维持 007 现状（baseline=' + dec['baseline_id'] + '）'}")
    for r in dec["reasons"]:
        print(f"   • {r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
