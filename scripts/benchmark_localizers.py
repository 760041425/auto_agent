#!/usr/bin/env python3
"""通过统一算法注册表运行可追踪的定位 benchmark。"""

from __future__ import annotations

import argparse
import glob
import hashlib
import html
import json
import platform
import random
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services.localizer.contracts import normalize_localization_result  # noqa: E402
from services.localizer.evaluation import compute_pose_error  # noqa: E402
from services.localizer.registry import (  # noqa: E402
    DEFAULT_ALGORITHM_REGISTRY,
    LocalizationInput,
)


ALGORITHM_ALIASES = {
    "disk_lg": "salad_roma_v2",
    "loftr": "salad_roma_v2_loftr",
    "ace": "ace_las",
    "multi": "multi_strategy",
}
DEFAULT_ALGORITHMS = [
    "salad_roma_v2",
    "salad_roma_v2_loftr",
    "hybrid",
    "ace_las",
    "multi_strategy",
]


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _device_name() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        pass
    return "cpu"


def build_manifest(
    query_paths: list[str],
    algorithm_ids: list[str],
    *,
    seed: int,
    ground_truth_path: str | None,
) -> dict[str, Any]:
    """构建每次唯一、配置部分稳定的 benchmark manifest。"""
    config = {
        "queries": [
            {"path": str(Path(path).resolve()), "sha256": _file_sha256(path)}
            for path in query_paths
        ],
        "algorithms": algorithm_ids,
        "seed": seed,
        "ground_truth": (
            {
                "path": str(Path(ground_truth_path).resolve()),
                "sha256": _file_sha256(ground_truth_path),
            }
            if ground_truth_path
            else None
        ),
        "fov_deg": 75.0,
        "min_inliers": 12,
    }
    encoded = json.dumps(config, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return {
        "schema_version": 1,
        "run_id": f"bench-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config_hash": hashlib.sha256(encoded).hexdigest(),
        "config": config,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "device": _device_name(),
            "git_commit": _git_commit(),
        },
    }


def load_ground_truth(path: str | None) -> dict[str, dict[str, Any]]:
    if not path:
        return {}
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    images = payload.get("images", payload)
    if not isinstance(images, dict):
        raise ValueError("ground truth must be an object keyed by query filename")
    return images


def run_single(
    query_path: str,
    algorithm_id: str,
    *,
    output_dir: str = "projections/benchmark",
    ground_truth_pose: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """用 API 同源 runner 运行一个算法并返回统一契约。"""
    started_at = time.perf_counter()
    try:
        definition = DEFAULT_ALGORITHM_REGISTRY.get(algorithm_id)
        raw_result = DEFAULT_ALGORITHM_REGISTRY.run(
            algorithm_id,
            LocalizationInput(
                image_path=query_path,
                output_dir=output_dir,
                fov_deg=75.0,
                min_inliers=12,
                geometric_verify=False,
            ),
        )
    except Exception as exc:
        definition = DEFAULT_ALGORITHM_REGISTRY.get(algorithm_id)
        raw_result = {
            "success": False,
            "error": {"code": "algorithm_exception", "message": str(exc)},
        }

    result = normalize_localization_result(
        algorithm_id,
        raw_result,
        min_inliers=12,
        elapsed_s=time.perf_counter() - started_at,
        feature_method=definition.feature_method,
    )
    result["query"] = Path(query_path).name
    if result["success"] and result.get("pose") and ground_truth_pose:
        result["validations"]["ground_truth"] = compute_pose_error(
            result["pose"], ground_truth_pose
        )
    return result


def _error_message(result: dict[str, Any]) -> str:
    error = result.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or "-")
    return str(error or "-")


def generate_report(
    results: list[dict[str, Any]],
    manifest: dict[str, Any],
    output_path: str,
) -> str:
    """生成候选观测报告；无真值时绝不输出最终推荐。"""
    has_ground_truth = any(
        result.get("validations", {}).get("ground_truth", {}).get("status") == "available"
        for result in results
    )
    rows = []
    for result in results:
        quality = result.get("quality", {})
        truth = result.get("validations", {}).get("ground_truth", {})
        status = "可信" if result.get("reliable") else ("低可信" if result.get("success") else "失败")
        rows.append(
            "<tr>"
            f"<td>{html.escape(result['query'])}</td>"
            f"<td>{html.escape(result['algorithm_id'])}</td>"
            f"<td>{status}</td>"
            f"<td>{quality.get('match_count', 0)}</td>"
            f"<td>{quality.get('inlier_count', 0)}</td>"
            f"<td>{quality.get('reprojection_error_px') if quality.get('reprojection_error_px') is not None else '-'}</td>"
            f"<td>{truth.get('translation_error_m', '-')}</td>"
            f"<td>{truth.get('rotation_error_deg', '-')}</td>"
            f"<td>{result.get('timings', {}).get('total_s', 0):.2f}s</td>"
            f"<td>{html.escape(_error_message(result))}</td>"
            "</tr>"
        )

    evidence_note = (
        "已加载独立真值；本报告给出候选观测，仍需按规格定义样本门槛并完成人工评审。"
        if has_ground_truth
        else "未提供独立真值：平移/旋转绝对误差不可用，本报告不能给出最终算法推荐。"
    )
    document = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><title>定位候选观测报告</title>
<style>
body {{ font-family: -apple-system, sans-serif; margin: 1rem; background:#f5f5f5; color:#333 }}
.container {{ max-width:1200px; margin:auto }} .card {{ background:white; padding:1rem; border-radius:8px; margin:1rem 0 }}
.gap {{ background:#fff8e1; border-left:4px solid #ff9800 }} table {{ width:100%; border-collapse:collapse; background:white }}
th,td {{ padding:8px; border:1px solid #eee; text-align:center }} th {{ background:#1976d2; color:white }}
</style></head><body><div class="container">
<h1>定位候选观测报告</h1>
<div class="card"><b>run_id:</b> {manifest['run_id']}<br><b>config_hash:</b> {manifest['config_hash']}<br>
<b>commit:</b> {manifest['environment'].get('git_commit') or 'unavailable'}<br><b>device:</b> {manifest['environment']['device']}</div>
<div class="card gap"><b>证据说明：</b>{evidence_note}</div>
<h2>逐次观测</h2><table><tr><th>Query</th><th>算法</th><th>状态</th><th>匹配</th><th>内点</th>
<th>重投影误差(px)</th><th>平移误差(m)</th><th>旋转误差(°)</th><th>耗时</th><th>错误</th></tr>
{''.join(rows)}</table>
<h2>观测汇总（非推荐）</h2>
<p>成功、可信和延迟可用于发现候选方案；只有独立 holdout 真值和样本门槛满足后，才能比较绝对精度。</p>
</div></body></html>"""
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(document, encoding="utf-8")
    return str(destination)


def _parse_algorithms(value: str) -> list[str]:
    requested = DEFAULT_ALGORITHMS if value == "all" else value.split(",")
    algorithm_ids = [ALGORITHM_ALIASES.get(item.strip(), item.strip()) for item in requested]
    unknown = [item for item in algorithm_ids if item not in DEFAULT_ALGORITHM_REGISTRY.ids()]
    if unknown:
        raise ValueError(f"unknown algorithms: {', '.join(unknown)}")
    return list(dict.fromkeys(algorithm_ids))


def main() -> None:
    parser = argparse.ArgumentParser(description="统一定位算法 benchmark")
    parser.add_argument("--queries", required=True, help="query 图像路径（支持通配符）")
    parser.add_argument("--algos", default="all", help="稳定算法 ID，逗号分隔，或 all")
    parser.add_argument("--ground-truth", help="以 query 文件名为 key 的独立位姿真值 JSON")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", help="HTML 输出路径；默认写入 reports/generated/")
    args = parser.parse_args()

    query_paths = sorted(glob.glob(args.queries))
    if not query_paths:
        parser.error(f"no images match {args.queries}")
    try:
        algorithm_ids = _parse_algorithms(args.algos)
        ground_truth = load_ground_truth(args.ground_truth)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    random.seed(args.seed)
    np.random.seed(args.seed)
    try:
        import torch

        torch.manual_seed(args.seed)
    except ImportError:
        pass

    manifest = build_manifest(
        query_paths,
        algorithm_ids,
        seed=args.seed,
        ground_truth_path=args.ground_truth,
    )
    output_path = args.output or f"reports/generated/{manifest['run_id']}.html"
    results = []
    for query_path in query_paths:
        for algorithm_id in algorithm_ids:
            print(f"{Path(query_path).name} × {algorithm_id} ...", end=" ", flush=True)
            result = run_single(
                query_path,
                algorithm_id,
                ground_truth_pose=ground_truth.get(Path(query_path).name),
            )
            print("可信" if result["reliable"] else ("低可信" if result["success"] else "失败"))
            results.append(result)

    report_path = generate_report(results, manifest, output_path)
    json_path = str(Path(report_path).with_suffix(".json"))
    Path(json_path).write_text(
        json.dumps({"manifest": manifest, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"报告: {report_path}")
    print(f"结构化数据: {json_path}")


if __name__ == "__main__":
    main()
