"""空间定位 benchmark 的防污染领域规则。"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import math
from statistics import mean, median
from typing import Any


class SelfMatchLeakError(ValueError):
    """leave-one-out 查询仍存在于检索索引。"""


class BenchmarkCoverageError(ValueError):
    """benchmark 样本覆盖不足，不能生成看似完整的结论。"""


def assess_normal_candidate(
    tile_unoriented_errors_deg: Mapping[str, Sequence[float]],
    *,
    required_tiles: int = 3,
    max_median_deg: float = 20.0,
) -> dict[str, Any]:
    """按跨 tile 总体无向角中位数判断法线候选能否进入软评分。"""
    valid_by_tile = {
        str(tile): [float(value) for value in values if math.isfinite(float(value))]
        for tile, values in tile_unoriented_errors_deg.items()
    }
    valid_by_tile = {tile: values for tile, values in valid_by_tile.items() if values}
    if len(valid_by_tile) < required_tiles:
        raise BenchmarkCoverageError(
            f"normal candidate 需要 {required_tiles} 个有效 tile，实际只有 {len(valid_by_tile)} 个"
        )

    errors = [value for values in valid_by_tile.values() for value in values]
    median_error = float(median(errors))
    return {
        "observed_tiles": len(valid_by_tile),
        "sample_count": len(errors),
        "median_unoriented_error_deg": median_error,
        "max_median_deg": float(max_median_deg),
        "eligible_for_soft_scoring": median_error <= max_median_deg,
    }


def summarize_normal_training_coverage(
    rows: Sequence[Mapping[str, Any]], *, required_tiles: int = 8
) -> dict[str, Any]:
    """汇总 ACE 监督像素及其法向输入覆盖，禁止混淆两种口径。"""
    tiles = {str(row.get("tile", "")) for row in rows if row.get("tile")}
    if len(tiles) < required_tiles:
        raise BenchmarkCoverageError(
            f"normal coverage 需要 {required_tiles} 个不同 tile，实际只有 {len(tiles)} 个"
        )

    xyz_total = sum(int(row["xyz_supervision_pixels"]) for row in rows)
    published_total = sum(
        int(row["published_normal_on_supervised_pixels"]) for row in rows
    )
    candidate_total = sum(
        int(row["candidate_normal_on_supervised_pixels"]) for row in rows
    )
    if xyz_total <= 0:
        raise BenchmarkCoverageError("XYZ 监督像素必须大于 0")
    if not (0 <= published_total <= xyz_total and 0 <= candidate_total <= xyz_total):
        raise BenchmarkCoverageError("法向有效像素必须是 XYZ 监督像素的子集")

    return {
        "observed_tiles": len(tiles),
        "xyz_supervision_pixels": xyz_total,
        "supervision_pixel_delta": 0,
        "published_normal_on_supervised_pixels": published_total,
        "candidate_normal_on_supervised_pixels": candidate_total,
        "published_normal_coverage_on_supervision": published_total / xyz_total,
        "candidate_normal_coverage_on_supervision": candidate_total / xyz_total,
        "candidate_vs_published_coverage_delta": (
            candidate_total - published_total
        )
        / xyz_total,
        "interpretation": "normal_input_coverage_only",
    }


def select_postprocessing_plan(*, pose_only_benchmark: bool) -> dict[str, bool]:
    """选择定位后处理；pose-only 只保留参与位姿评分的主链路。"""
    enabled = not pose_only_benchmark
    return {
        "dense_point_cloud": enabled,
        "las_verification": enabled,
        "projection_verification": enabled,
        "coordinate_transform": enabled,
        "visual_artifacts": enabled,
    }


def load_dense_map_assets(
    postprocessing_plan: Mapping[str, bool], loader: Callable[[], Any]
) -> Any | None:
    """仅在后处理需要稠密地图时触发昂贵的基础设施加载器。"""
    if not postprocessing_plan.get("dense_point_cloud", True):
        return None
    return loader()


def require_leave_one_out(index: Mapping[str, Any], query_tile_key: str) -> None:
    """断言查询 tile 已从索引排除，否则阻止产生受污染报告。"""
    if query_tile_key in index:
        raise SelfMatchLeakError(
            f"leave-one-out 索引仍包含查询 tile key: {query_tile_key}"
        )


def exclude_query_tile(
    index: Mapping[str, Any], query_tile_key: str
) -> dict[str, Any]:
    """复制索引并排除查询 tile；不得修改定位进程共享缓存。"""
    filtered = dict(index)
    filtered.pop(query_tile_key, None)
    return filtered


def select_leave_one_out_tiles(
    entries: Sequence[Mapping[str, Any]], count: int
) -> list[Mapping[str, Any]]:
    """按地图顺序均匀抽取不同 tile，避免样本集中在单一区域。"""
    if count <= 0:
        raise BenchmarkCoverageError("leave_one_out 样本数必须大于 0")

    unique: list[Mapping[str, Any]] = []
    seen_tiles: set[str] = set()
    for entry in entries:
        tile = str(entry.get("tile", ""))
        if not entry.get("accepted") or not tile or tile in seen_tiles:
            continue
        if not entry.get("image_path") or not entry.get("camera_pose"):
            continue
        seen_tiles.add(tile)
        unique.append(entry)

    if len(unique) < count:
        raise BenchmarkCoverageError(
            f"leave_one_out 需要 {count} 个不同 tile，实际只有 {len(unique)} 个"
        )
    if count == 1:
        return [unique[0]]

    last = len(unique) - 1
    return [unique[(i * last) // (count - 1)] for i in range(count)]


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _summarize_group(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    cold = [float(row["elapsed_s"]) for row in rows if row.get("cold_start")]
    warm = [float(row["elapsed_s"]) for row in rows if not row.get("cold_start")]
    pose_rows = [
        row["pose_error"]
        for row in rows
        if isinstance(row.get("pose_error"), Mapping)
        and row["pose_error"].get("status") == "available"
        and row["pose_error"].get("translation_error_m") is not None
        and row["pose_error"].get("rotation_error_deg") is not None
    ]
    translation_errors = [float(row["translation_error_m"]) for row in pose_rows]
    rotation_errors = [float(row["rotation_error_deg"]) for row in pose_rows]
    total = len(rows)

    return {
        "n_total": total,
        "n_success": sum(bool(row.get("success")) for row in rows),
        "success_rate": sum(bool(row.get("success")) for row in rows) / total,
        "quality_pass_rate": sum(bool(row.get("quality_passed")) for row in rows)
        / total,
        "cold_latency_s": mean(cold) if cold else None,
        "warm_latency_p50_s": _percentile(warm, 0.50),
        "warm_latency_p95_s": _percentile(warm, 0.95),
        "ground_truth_n": len(pose_rows),
        "accuracy_status": "available" if pose_rows else "diagnostic_only",
        "translation_error_mean_m": mean(translation_errors)
        if translation_errors
        else None,
        "rotation_error_mean_deg": mean(rotation_errors) if rotation_errors else None,
    }


def summarize_validation_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    required_leave_one_out: int,
    required_real: int,
) -> dict[str, dict[str, Any]]:
    """分别统计有真值留一组和无真值真实组，禁止混算准确率。"""
    groups = {
        "leave_one_out": [
            row for row in rows if row.get("sample_type") == "leave_one_out"
        ],
        "real_query": [row for row in rows if row.get("sample_type") == "real_query"],
    }
    requirements = {
        "leave_one_out": required_leave_one_out,
        "real_query": required_real,
    }
    for name, required in requirements.items():
        if len(groups[name]) < required:
            raise BenchmarkCoverageError(
                f"{name} 需要至少 {required} 条结果，实际只有 {len(groups[name])} 条"
            )

    return {name: _summarize_group(group) for name, group in groups.items()}
