"""
快速定位算法 — 复用 SALAD 引擎确保正确性

策略：调用 SALAD v2_loftr 完整流程，最快参数。
虽然耗时 ~40s，但确保位姿正确（坐标变换正确）。
"""

import os
import time
from services.localizer.logger_config import get_backend_logger
from services.localizer.salad_roma_v2 import localize_with_salad_roma_v2

_logger = get_backend_logger("fast_localizer")


def log(msg: str):
    _logger.info(msg)


def localize_fast(image_path: str, output_dir: str = "projections/localize_fast",
                   fov_deg: float = 75.0, **kwargs) -> dict:
    """快速定位 — SALAD 引擎 + 最快参数（~40s，结果正确）"""
    tag = "fast"
    t0 = time.time()
    log(f"{'=' * 60}")
    log(f"🚀 快速定位: {os.path.basename(image_path)}")

    result = localize_with_salad_roma_v2(
        image_path,
        output_dir=output_dir,
        max_iterations=0,
        top_k_retrieval=1,
        matcher_mode="loftr",
        fov_deg=fov_deg,
        use_pose_prior=False,
        reproj_error=4.0,
        min_inliers=6,
        keep_aspect_ratio=True,
    )

    if isinstance(result, dict):
        result["tag"] = tag
        result["algorithm_label"] = "E. 快速定位"
        result["spatial_config"] = {"top_k": 1, "max_iterations": 0, "matcher_mode": "loftr"}
        if "timings" not in result:
            result["timings"] = {}
        result["timings"]["total_s"] = round(time.time() - t0, 2)

    return result
