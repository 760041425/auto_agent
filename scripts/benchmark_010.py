"""010 空间定位可信基准：8 个 leave-one-out tile + 2 个真实查询。

准确率只来自带独立真值的 leave-one-out 组；真实查询没有外部位姿真值，
只报告成功、质量门和耗时，避免用重投影误差冒充绝对精度。

示例：
    .venv/bin/python scripts/benchmark_010.py \
        --output reports/benchmark_010.json
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _reference_pose(entry: dict[str, Any]) -> dict[str, Any]:
    camera_pose = entry["camera_pose"]
    position = camera_pose["position_local_m"]
    return {
        "translation": [position["x"], position["y"], position["z"]],
        "quaternion": camera_pose["quaternion_wxyz"],
    }


def _run_query(
    image_path: str,
    *,
    output_dir: str,
    exclude_query_tile_key: str | None = None,
    fov_deg: float = 75.0,
    pose_only_benchmark: bool = False,
) -> tuple[dict[str, Any], float]:
    from services.localizer.salad_roma_v2 import localize_with_salad_roma_v2

    started = time.perf_counter()
    try:
        result = localize_with_salad_roma_v2(
            image_path,
            output_dir=output_dir,
            max_iterations=2,
            top_k_retrieval=3,
            debug_visualizations=False,
            matcher_mode="loftr",
            fov_deg=fov_deg,
            use_pose_prior=False,
            fast_mode=True,
            exclude_query_tile_key=exclude_query_tile_key,
            pose_only_benchmark=pose_only_benchmark,
        )
    except Exception as exc:
        result = {"success": False, "error": f"{type(exc).__name__}: {exc}"}
    return result, time.perf_counter() - started


def _base_row(
    *,
    sample_type: str,
    image_path: str,
    result: dict[str, Any],
    elapsed_s: float,
    cold_start: bool,
) -> dict[str, Any]:
    return {
        "sample_type": sample_type,
        "image": image_path,
        "success": bool(result.get("success")),
        "quality_passed": bool(result.get("quality_passed")),
        "elapsed_s": round(elapsed_s, 3),
        "cold_start": cold_start,
        "inliers": result.get("inliers", 0),
        "reprojection_error_px": result.get("reprojection_error"),
        "projection_verification": result.get("projection_verification"),
        "las_verification": result.get("las_verification"),
        "error": result.get("error"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="010 8+2 空间定位可信基准")
    parser.add_argument("--max-tiles", type=int, default=8)
    parser.add_argument("--max-real", type=int, default=2)
    parser.add_argument("--output", default="reports/benchmark_010.json")
    parser.add_argument("--output-dir", default="projections/benchmark_010")
    parser.add_argument(
        "--pose-only",
        action="store_true",
        help="跳过稠密地图、二次投影验证、LAS 验证、坐标一致性和视觉产物",
    )
    args = parser.parse_args()

    if args.max_tiles != 8 or args.max_real != 2:
        parser.error("正式 TL-010-08 基准固定要求 --max-tiles 8 --max-real 2")

    from services.localizer.evaluation import compute_pose_error
    from services.localizer.salad_roma_v2 import _ensure_index, _load_tile_index, _tile_key
    from services.localizer.spatial_validation import (
        BenchmarkCoverageError,
        exclude_query_tile,
        require_leave_one_out,
        select_leave_one_out_tiles,
        summarize_validation_rows,
    )

    init_started = time.perf_counter()
    source_index = _ensure_index()
    index_initialization_s = time.perf_counter() - init_started
    candidates = [
        entry for entry in _load_tile_index() if _tile_key(entry) in source_index
    ]
    selected = select_leave_one_out_tiles(candidates, count=args.max_tiles)
    real_queries = sorted(glob.glob("query_images/*.jpg"))[: args.max_real]
    if len(real_queries) < args.max_real:
        raise BenchmarkCoverageError(
            f"real_query 需要 {args.max_real} 张，实际只有 {len(real_queries)} 张"
        )

    rows: list[dict[str, Any]] = []
    total = len(selected) + len(real_queries)
    print(
        f"010 查询集：{len(selected)} leave-one-out + {len(real_queries)} real；"
        f"索引初始化 {index_initialization_s:.2f}s"
    )

    for sequence, entry in enumerate(selected):
        query_key = _tile_key(entry)
        prepared_index = exclude_query_tile(source_index, query_key)
        require_leave_one_out(prepared_index, query_key)
        sample_dir = os.path.join(args.output_dir, f"loo-{sequence:02d}")
        result, localization_s = _run_query(
            entry["image_path"],
            output_dir=sample_dir,
            exclude_query_tile_key=query_key,
            fov_deg=float(entry.get("camera", {}).get("fov_deg", 75.0)),
            pose_only_benchmark=args.pose_only,
        )
        elapsed_s = localization_s + (index_initialization_s if sequence == 0 else 0.0)
        row = _base_row(
            sample_type="leave_one_out",
            image_path=entry["image_path"],
            result=result,
            elapsed_s=elapsed_s,
            cold_start=sequence == 0,
        )
        row.update(
            {
                "query_tile_key": query_key,
                "tile": entry["tile"],
                "view": entry.get("view"),
                "self_match_excluded": query_key not in prepared_index,
                "pose_error": compute_pose_error(result["pose"], _reference_pose(entry))
                if result.get("success") and result.get("pose")
                else {"status": "unavailable", "reason": "localization_failed"},
            }
        )
        rows.append(row)
        status = "✓" if row["success"] else "✗"
        print(
            f"  [{sequence + 1}/{total}] LOO {entry['tile']} {entry.get('view')}: "
            f"{status} {elapsed_s:.2f}s"
        )

    for offset, image_path in enumerate(real_queries, start=len(selected)):
        result, elapsed_s = _run_query(
            image_path,
            output_dir=os.path.join(args.output_dir, f"real-{offset - len(selected):02d}"),
            pose_only_benchmark=args.pose_only,
        )
        row = _base_row(
            sample_type="real_query",
            image_path=image_path,
            result=result,
            elapsed_s=elapsed_s,
            cold_start=False,
        )
        row.update(
            {
                "self_match_excluded": None,
                "pose_error": {
                    "status": "unavailable",
                    "reason": "no_independent_ground_truth",
                },
            }
        )
        rows.append(row)
        status = "✓" if row["success"] else "✗"
        print(f"  [{offset + 1}/{total}] REAL {Path(image_path).name}: {status} {elapsed_s:.2f}s")

    summary = summarize_validation_rows(
        rows,
        required_leave_one_out=args.max_tiles,
        required_real=args.max_real,
    )
    report = {
        "run_id": f"bench-010-{time.strftime('%Y%m%d-%H%M%S')}",
        "algorithm": "salad_v2_loftr_fast",
        "protocol": {
            "leave_one_out": args.max_tiles,
            "real_query": args.max_real,
            "self_match_policy": "query_tile_key_excluded_from_retrieval_copy",
            "accuracy_policy": "pose error only for leave_one_out ground truth",
            "pose_only_benchmark": args.pose_only,
            "dense_map_policy": (
                "skipped_with_las_verification"
                if args.pose_only
                else "loaded_for_complete_postprocessing"
            ),
            "projection_verification_policy": (
                "skipped" if args.pose_only else "enabled"
            ),
            "pnp_ransac_seed": 1337,
            "index_initialization_s_included_in_first_cold_row": round(
                index_initialization_s, 3
            ),
        },
        "rows": rows,
        "summary": summary,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"报告：{output.resolve()}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
