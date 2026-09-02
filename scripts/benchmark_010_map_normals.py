"""TL-010-13/14：生成隔离地图法线候选并重跑覆盖与 MoGe 资格门。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from statistics import median
from typing import Any

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _stencil_valid(world_xyz: np.ndarray) -> np.ndarray:
    source_valid = np.isfinite(world_xyz).all(axis=-1) & (
        np.linalg.norm(world_xyz, axis=-1) > 1e-6
    )
    stencil = np.zeros(source_valid.shape, dtype=bool)
    stencil[1:-1, 1:-1] = (
        source_valid[1:-1, 1:-1]
        & source_valid[:-2, 1:-1]
        & source_valid[2:, 1:-1]
        & source_valid[1:-1, :-2]
        & source_valid[1:-1, 2:]
    )
    return stencil


def _ace_low_resolution_masks(
    world_xyz: np.ndarray,
    published_normal: np.ndarray,
    candidate_normal: np.ndarray,
    *,
    image_height: int,
    image_width: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """复刻 SceneCoordinateDataset 的最近邻缩放与 1/8 监督采样。"""
    target_height = (image_height // 32) * 32
    target_width = (image_width // 32) * 32
    if target_height <= 0 or target_width <= 0:
        raise ValueError("ACE 输入尺寸必须至少为 32x32")

    xyz = cv2.resize(
        world_xyz, (target_width, target_height), interpolation=cv2.INTER_NEAREST
    )[::8, ::8]
    published = cv2.resize(
        published_normal,
        (target_width, target_height),
        interpolation=cv2.INTER_NEAREST,
    )[::8, ::8]
    candidate = cv2.resize(
        candidate_normal,
        (target_width, target_height),
        interpolation=cv2.INTER_NEAREST,
    )[::8, ::8]
    xyz_valid = np.isfinite(xyz).all(axis=-1) & (np.linalg.norm(xyz, axis=-1) > 1e-6)
    published_valid = np.isfinite(published).all(axis=-1) & (
        np.linalg.norm(published, axis=-1) > 0.5
    )
    candidate_valid = np.isfinite(candidate).all(axis=-1) & (
        np.linalg.norm(candidate, axis=-1) > 0.5
    )
    return xyz_valid, published_valid, candidate_valid


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", default="reports/benchmark_010_map_normals_8.json"
    )
    parser.add_argument(
        "--candidate-dir", default="projections/benchmark_010_map_normals_8"
    )
    parser.add_argument(
        "--cache-dir", default="projections/model_cache/huggingface"
    )
    parser.add_argument("--device", default="cpu", choices=("cpu", "mps", "cuda"))
    parser.add_argument("--num-tokens", type=int, default=1200)
    parser.add_argument("--tile-count", type=int, default=8)
    args = parser.parse_args()

    from scripts.benchmark_010_moge_normals import _compare_with_map
    from services.las_processor.projection_octree import _compute_normal_map
    from services.localizer.moge_normal_010 import (
        load_moge_normal_model,
        predict_moge_normals,
    )
    from services.localizer.spatial_validation import (
        assess_normal_candidate,
        select_leave_one_out_tiles,
        summarize_normal_training_coverage,
    )

    with open("projections/tile_index.json", encoding="utf-8") as file:
        entries = json.load(file)
    candidates = [
        entry
        for entry in entries
        if all(
            path and Path(path).is_file()
            for path in (
                entry.get("image_path"),
                entry.get("npy_path"),
                entry.get("normal_path"),
            )
        )
    ]
    tiles = select_leave_one_out_tiles(candidates, count=args.tile_count)

    candidate_dir = Path(args.candidate_dir)
    candidate_dir.mkdir(parents=True, exist_ok=True)
    model, model_metadata = load_moge_normal_model(
        cache_dir=args.cache_dir, device=args.device
    )
    rows: list[dict[str, Any]] = []
    model_errors: dict[str, list[float]] = {}
    legacy_errors: dict[str, list[float]] = {}

    for sequence, entry in enumerate(tiles):
        world_xyz = np.load(entry["npy_path"])
        legacy_normal = np.load(entry["normal_path"])
        started = time.perf_counter()
        candidate_normal = _compute_normal_map(world_xyz)
        generation_s = time.perf_counter() - started

        candidate_path = candidate_dir / (
            f"{Path(entry['image_path']).stem}_normal_candidate.npy"
        )
        if candidate_path.resolve() == Path(entry["normal_path"]).resolve():
            raise RuntimeError("候选法线不得覆盖发布 normal_path")
        np.save(candidate_path, candidate_normal)

        candidate_valid = np.linalg.norm(candidate_normal, axis=-1) > 0.5
        legacy_valid = np.linalg.norm(legacy_normal, axis=-1) > 0.5
        stencil_valid = _stencil_valid(world_xyz)
        false_valid = int(np.count_nonzero(candidate_valid & ~stencil_valid))
        legacy_comparison, legacy_angle = _compare_with_map(
            legacy_normal,
            legacy_valid,
            candidate_normal,
            [1.0, 0.0, 0.0, 0.0],
        )

        image_bgr = cv2.imread(entry["image_path"], cv2.IMREAD_COLOR)
        if image_bgr is None:
            raise FileNotFoundError(entry["image_path"])
        xyz_low, published_low, candidate_low = _ace_low_resolution_masks(
            world_xyz,
            legacy_normal,
            candidate_normal,
            image_height=image_bgr.shape[0],
            image_width=image_bgr.shape[1],
        )
        inference_started = time.perf_counter()
        prediction = predict_moge_normals(
            model,
            cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB),
            device=args.device,
            num_tokens=args.num_tokens,
        )
        inference_s = time.perf_counter() - inference_started
        model_comparison, model_angle = _compare_with_map(
            prediction["normal"],
            prediction["valid_mask"],
            candidate_normal,
            entry["camera_pose"]["quaternion_wxyz"],
        )

        tile_key = str(entry["tile"])
        legacy_errors[tile_key] = legacy_angle.tolist()
        model_errors[tile_key] = model_angle.tolist()
        row = {
            "tile": entry["tile"],
            "view": entry.get("view"),
            "image_path": entry["image_path"],
            "xyz_path": entry["npy_path"],
            "published_normal_path": entry["normal_path"],
            "candidate_normal_path": str(candidate_path),
            "published_asset_unchanged": True,
            "generation_s": round(generation_s, 4),
            "moge_inference_s": round(inference_s, 3),
            "published_valid_rate": float(legacy_valid.mean()),
            "candidate_valid_rate": float(candidate_valid.mean()),
            "stencil_valid_rate": float(stencil_valid.mean()),
            "false_valid_outside_stencil": false_valid,
            "xyz_supervision_pixels": int(np.count_nonzero(xyz_low)),
            "published_normal_on_supervised_pixels": int(
                np.count_nonzero(xyz_low & published_low)
            ),
            "candidate_normal_on_supervised_pixels": int(
                np.count_nonzero(xyz_low & candidate_low)
            ),
            "published_vs_candidate": legacy_comparison,
            "moge_vs_candidate": model_comparison,
        }
        rows.append(row)
        print(
            f"[{sequence + 1}/{args.tile_count}] {tile_key}: "
            f"candidate={row['candidate_valid_rate']:.1%}，"
            f"false={false_valid}，MoGe={model_comparison['unoriented_median_deg']:.2f}°"
        )

    legacy_change = assess_normal_candidate(
        legacy_errors, required_tiles=args.tile_count, max_median_deg=10.0
    )
    model_eligibility = assess_normal_candidate(
        model_errors, required_tiles=args.tile_count, max_median_deg=20.0
    )
    training_coverage = summarize_normal_training_coverage(
        rows, required_tiles=args.tile_count
    )
    report = {
        "run_id": f"bench-010-map-normal-{time.strftime('%Y%m%d-%H%M%S')}",
        "protocol": {
            "tile": args.tile_count,
            "distinct_spatial_positions": len({str(row["tile"]) for row in rows}),
            "candidate_policy": "isolated_copy_only; tile_index and published normal unchanged",
            "normal_contract": "complete four-neighbor stencil + central difference",
            "ace_sampling_contract": "nearest resize to multiples of 32, then [::8, ::8]; XYZ alone defines supervision",
            "ace_training_executed": False,
            "moge_gate": "overall unoriented median <= 20 deg",
            "num_tokens": args.num_tokens,
        },
        "model": model_metadata,
        "rows": rows,
        "summary": {
            "n_total": len(rows),
            "all_candidates_written": all(
                Path(row["candidate_normal_path"]).is_file() for row in rows
            ),
            "published_assets_unchanged": all(
                row["published_asset_unchanged"] for row in rows
            ),
            "false_valid_outside_stencil": sum(
                row["false_valid_outside_stencil"] for row in rows
            ),
            "generation_p50_s": float(median(row["generation_s"] for row in rows)),
            "moge_inference_p50_s": float(
                median(row["moge_inference_s"] for row in rows)
            ),
            "published_vs_candidate": legacy_change,
            "moge_eligibility": model_eligibility,
            "ace_training_coverage": training_coverage,
        },
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"报告：{output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
