"""
超快定位算法 — 复用 SALAD 引擎确保正确性

策略：调用 SALAD v2_loftr 完整流程，最小参数。
- top_k=1（只匹配 1 个 tile）
- max_iterations=0（无迭代精化）
- 单次匹配 + 单次 PnP
- 包含坐标变换和验证（确保位姿正确）

注意：虽然名为"超快"，但由于 SALAD 包含坐标验证，实际耗时 ~30-40s。
如需真正 < 5s，需要跳过验证（但位姿可能不正确）。
"""

import os
import time
from services.localizer.logger_config import get_backend_logger
from services.localizer.salad_roma_v2 import localize_with_salad_roma_v2

_logger = get_backend_logger("ultrafast_localizer")


def log(msg: str):
    _logger.info(msg)


def localize_ultrafast(image_path: str, output_dir: str = "projections/localize_ultrafast",
                        fov_deg: float = 75.0, **kwargs) -> dict:
    """超快定位 — SALAD 引擎 + 最小参数（~30-40s，结果正确）"""
    tag = "ultrafast"
    t0 = time.time()
    log(f"{'=' * 60}")
    log(f"🚀 超快定位: {os.path.basename(image_path)}")

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
        result["algorithm_label"] = "F. 超快定位"
        result["spatial_config"] = {"top_k": 1, "max_iterations": 0, "matcher_mode": "loftr"}
        if "timings" not in result:
            result["timings"] = {}
        result["timings"]["total_s"] = round(time.time() - t0, 2)

    return result
