"""TL-010-12：MoGe-2 ViT-S normal 的 2 查询 + 3 tile 轻量资格验证。"""

from __future__ import annotations

import argparse
import glob
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


def _normal_contract(normal: np.ndarray, valid_mask: np.ndarray) -> dict[str, Any]:
    lengths = np.linalg.norm(normal, axis=-1)
    finite = np.isfinite(normal).all(axis=-1)
    return {
        "shape": list(normal.shape),
        "dtype": str(normal.dtype),
        "finite_rate": float(finite.mean()),
        "valid_rate": float(valid_mask.mean()),
        "valid_length_mean": float(lengths[valid_mask].mean())
        if valid_mask.any()
        else None,
    }


def _raw_world_normals(world_xyz: np.ndarray) -> np.ndarray:
    """按地图准备的唯一四邻域契约，从权威 XYZ 生成候选参考法线。"""
    from services.las_processor.projection_octree import _compute_normal_map

    return _compute_normal_map(world_xyz)


def _compare_with_map(
    predicted_camera: np.ndarray,
    predicted_valid: np.ndarray,
    world_normal: np.ndarray,
    world_to_camera_wxyz: list[float],
) -> tuple[dict[str, Any], np.ndarray]:
    from services.localizer.pose_utils import quaternion_to_rotation_matrix

    if predicted_camera.shape != world_normal.shape:
        raise ValueError(
            f"预测/地图法线 shape 不一致: {predicted_camera.shape} != {world_normal.shape}"
        )
    rotation_cw = quaternion_to_rotation_matrix(
        np.asarray(world_to_camera_wxyz, dtype=np.float64)
    )
    map_camera = world_normal.astype(np.float64) @ rotation_cw.T
    map_lengths = np.linalg.norm(map_camera, axis=-1)
    predicted_lengths = np.linalg.norm(predicted_camera, axis=-1)
    overlap = (
        predicted_valid
        & np.isfinite(map_camera).all(axis=-1)
        & (map_lengths > 1e-10)
        & (predicted_lengths > 0.5)
    )
    if not overlap.any():
        raise ValueError("预测法线与地图法线没有有效重叠像素")
    map_unit = map_camera[overlap] / map_lengths[overlap, None]
    predicted_unit = (
        predicted_camera[overlap].astype(np.float64)
        / predicted_lengths[overlap, None]
    )
    dots = np.clip(np.sum(predicted_unit * map_unit, axis=-1), -1.0, 1.0)
    directed = np.degrees(np.arccos(dots))
    unoriented = np.degrees(np.arccos(np.abs(dots)))
    return {
        "overlap_pixels": int(overlap.sum()),
        "overlap_rate": float(overlap.mean()),
        "directed_median_deg": float(np.median(directed)),
        "unoriented_median_deg": float(np.median(unoriented)),
        "unoriented_p90_deg": float(np.quantile(unoriented, 0.90)),
    }, unoriented


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="reports/benchmark_010_moge_normals.json")
    parser.add_argument(
        "--cache-dir", default="projections/model_cache/huggingface"
    )
    parser.add_argument("--device", default="cpu", choices=("cpu", "mps", "cuda"))
    parser.add_argument("--num-tokens", type=int, default=1200)
    args = parser.parse_args()

    from services.localizer.moge_normal_010 import (
        NORMAL_COORDINATE_FRAME,
        NORMAL_SOURCE,
        load_moge_normal_model,
        predict_moge_normals,
    )
    from services.localizer.spatial_validation import assess_normal_candidate

    with open("projections/tile_index.json", encoding="utf-8") as file:
        accepted = [entry for entry in json.load(file) if entry.get("accepted")]
    tiles = accepted[:3]
    real_queries = sorted(glob.glob("query_images/*.jpg"))[:2]
    if len(tiles) != 3 or len(real_queries) != 2:
        raise RuntimeError("TL-010-12 固定要求 3 个 accepted tile + 2 张真实查询")

    load_started = time.perf_counter()
    model, model_metadata = load_moge_normal_model(
        cache_dir=args.cache_dir, device=args.device
    )
    model_load_s = time.perf_counter() - load_started
    print(
        f"MoGe-2 已加载：{model_metadata['checkpoint_bytes'] / 1024 / 1024:.1f}MB，"
        f"{model_load_s:.2f}s，device={args.device}"
    )

    samples: list[tuple[str, str, dict[str, Any] | None]] = [
        ("real_query", path, None) for path in real_queries
    ] + [("tile", entry["image_path"], entry) for entry in tiles]
    rows: list[dict[str, Any]] = []
    angle_errors: dict[str, list[float]] = {}
    raw_xyz_angle_errors: dict[str, list[float]] = {}
    reference_angle_errors: dict[str, list[float]] = {}

    for sequence, (sample_type, image_path, entry) in enumerate(samples):
        image_bgr = cv2.imread(image_path, cv2.IMREAD_COLOR)
        if image_bgr is None:
            raise FileNotFoundError(image_path)
        image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        started = time.perf_counter()
        prediction = predict_moge_normals(
            model,
            image_rgb,
            device=args.device,
            num_tokens=args.num_tokens,
        )
        inference_s = time.perf_counter() - started
        row = {
            "sample_type": sample_type,
            "image": image_path,
            "cold_start": sequence == 0,
            "inference_s": round(inference_s, 3),
            "normal_source": prediction["normal_source"],
            "coordinate_frame": prediction["coordinate_frame"],
            "contract": _normal_contract(
                prediction["normal"], prediction["valid_mask"]
            ),
        }
        if entry is not None:
            stored_world_normal = np.load(entry["normal_path"])
            comparison, errors = _compare_with_map(
                prediction["normal"],
                prediction["valid_mask"],
                stored_world_normal,
                entry["camera_pose"]["quaternion_wxyz"],
            )
            raw_world_normal = _raw_world_normals(np.load(entry["npy_path"]))
            raw_comparison, raw_errors = _compare_with_map(
                prediction["normal"],
                prediction["valid_mask"],
                raw_world_normal,
                entry["camera_pose"]["quaternion_wxyz"],
            )
            stored_valid = np.linalg.norm(stored_world_normal, axis=-1) > 0.5
            reference_comparison, reference_errors = _compare_with_map(
                stored_world_normal,
                stored_valid,
                raw_world_normal,
                [1.0, 0.0, 0.0, 0.0],
            )
            row.update(
                {
                    "tile": entry["tile"],
                    "view": entry.get("view"),
                    "normal_path": entry["normal_path"],
                    "comparison": comparison,
                    "raw_xyz_comparison": raw_comparison,
                    "stored_vs_raw_reference": reference_comparison,
                }
            )
            tile_key = str(entry.get("view") or entry["tile"])
            angle_errors[tile_key] = errors.tolist()
            raw_xyz_angle_errors[tile_key] = raw_errors.tolist()
            reference_angle_errors[tile_key] = reference_errors.tolist()
        rows.append(row)
        print(
            f"  [{sequence + 1}/5] {sample_type} {Path(image_path).name}: "
            f"{inference_s:.2f}s，valid={row['contract']['valid_rate']:.1%}"
        )

    eligibility = assess_normal_candidate(
        angle_errors, required_tiles=3, max_median_deg=20.0
    )
    raw_xyz_diagnostic = assess_normal_candidate(
        raw_xyz_angle_errors, required_tiles=3, max_median_deg=20.0
    )
    reference_audit = assess_normal_candidate(
        reference_angle_errors, required_tiles=3, max_median_deg=20.0
    )
    report = {
        "run_id": f"bench-010-moge-{time.strftime('%Y%m%d-%H%M%S')}",
        "protocol": {
            "real_query": 2,
            "tile": 3,
            "num_tokens": args.num_tokens,
            "normal_source": NORMAL_SOURCE,
            "coordinate_frame": NORMAL_COORDINATE_FRAME,
            "soft_score_gate": "overall unoriented median <= 20 deg",
        },
        "model": model_metadata,
        "model_load_s": round(model_load_s, 3),
        "rows": rows,
        "summary": {
            "n_total": len(rows),
            "n_finite": sum(row["contract"]["finite_rate"] == 1.0 for row in rows),
            "warm_inference_p50_s": float(
                median(row["inference_s"] for row in rows if not row["cold_start"])
            ),
            "eligibility": eligibility,
            "raw_xyz_diagnostic": raw_xyz_diagnostic,
            "stored_normal_vs_raw_xyz_audit": reference_audit,
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
