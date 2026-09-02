"""010 hloc-lite 官方前端基准：SALAD 同候选 + SuperPoint/LightGlue + NPY/PnP。"""

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
if not os.environ.get("SSL_CERT_FILE") and Path("/etc/ssl/cert.pem").exists():
    os.environ["SSL_CERT_FILE"] = "/etc/ssl/cert.pem"


def _reference_pose(entry: dict[str, Any]) -> dict[str, Any]:
    pose = entry["camera_pose"]
    position = pose["position_local_m"]
    return {
        "translation": [position["x"], position["y"], position["z"]],
        "quaternion": pose["quaternion_wxyz"],
    }


def _row(
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
        "match_count": result.get("match_count", 0),
        "correspondence_count": result.get("correspondence_count", 0),
        "reprojection_error_px": result.get("reprojection_error"),
        "retrieved_tile_key": result.get("retrieved_tile_key"),
        "error": result.get("error"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="010 hloc-lite 8+2 基准")
    parser.add_argument("--smoke", action="store_true", help="只跑第一个留一样本")
    parser.add_argument("--output", default="reports/benchmark_010_hloc.json")
    args = parser.parse_args()

    from services.localizer.evaluation import compute_pose_error
    from services.localizer.hloc_baseline_010 import (
        localize_with_hloc_frontend_010,
        probe_hloc_dependencies,
    )
    from services.localizer.salad_roma_v2 import _ensure_index, _load_tile_index, _tile_key
    from services.localizer.spatial_validation import (
        exclude_query_tile,
        require_leave_one_out,
        select_leave_one_out_tiles,
        summarize_validation_rows,
    )

    dependency_status = probe_hloc_dependencies()
    if dependency_status["frontend_status"] != "available":
        print(json.dumps(dependency_status, ensure_ascii=False, indent=2))
        return 3

    init_started = time.perf_counter()
    source_index = _ensure_index()
    index_initialization_s = time.perf_counter() - init_started
    candidates = [entry for entry in _load_tile_index() if _tile_key(entry) in source_index]
    selected = select_leave_one_out_tiles(candidates, count=8)
    real_queries = sorted(glob.glob("query_images/*.jpg"))[:2]
    if args.smoke:
        selected = selected[:1]
        real_queries = []

    rows: list[dict[str, Any]] = []
    samples: list[tuple[str, Any]] = [("leave_one_out", entry) for entry in selected]
    samples += [("real_query", path) for path in real_queries]
    for sequence, (sample_type, sample) in enumerate(samples):
        if sample_type == "leave_one_out":
            entry = sample
            key = _tile_key(entry)
            prepared = exclude_query_tile(source_index, key)
            require_leave_one_out(prepared, key)
            image_path = entry["image_path"]
            fov_deg = float(entry.get("camera", {}).get("fov_deg", 75.0))
        else:
            entry = None
            key = None
            image_path = sample
            fov_deg = 75.0

        started = time.perf_counter()
        try:
            result = localize_with_hloc_frontend_010(
                image_path,
                exclude_query_tile_key=key,
                fov_deg=fov_deg,
            )
        except Exception as exc:
            result = {"success": False, "error": f"{type(exc).__name__}: {exc}"}
        elapsed_s = time.perf_counter() - started
        if sequence == 0:
            elapsed_s += index_initialization_s
        row = _row(sample_type, image_path, result, elapsed_s, sequence == 0)
        if entry is not None:
            row.update(
                {
                    "query_tile_key": key,
                    "tile": entry["tile"],
                    "view": entry.get("view"),
                    "self_match_excluded": key not in prepared,
                    "pose_error": compute_pose_error(result["pose"], _reference_pose(entry))
                    if result.get("success") and result.get("pose")
                    else {"status": "unavailable", "reason": "localization_failed"},
                }
            )
        else:
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
        print(
            f"[{sequence + 1}/{len(samples)}] {sample_type} {Path(image_path).name}: "
            f"{'✓' if row['success'] else '✗'} {elapsed_s:.2f}s "
            f"matches={row['match_count']} inliers={row['inliers']}"
        )

    summary = None
    if not args.smoke:
        summary = summarize_validation_rows(
            rows, required_leave_one_out=8, required_real=2
        )
    report = {
        "run_id": f"bench-010-hloc-{time.strftime('%Y%m%d-%H%M%S')}",
        "algorithm": "hloc_superpoint_lightglue_010",
        "scope": "smoke" if args.smoke else "8_leave_one_out_plus_2_real",
        "dependency_status": dependency_status,
        "index_initialization_s": round(index_initialization_s, 3),
        "rows": rows,
        "summary": summary,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"报告：{output.resolve()}")
    if summary:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
