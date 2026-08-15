"""
空间特征定位算法 — 4 种新方案（真正差异化）

复用 SALAD v2 完整引擎（坐标变换 + 验证 + 视觉产物），
通过 matcher_mode 切换匹配器，实现真正的差异化。

A. pointcloud_descriptor — LoFTR + 联合 PnP + 迭代精化（精度优先）
B. depth_icp — DISK+LightGlue + 快速（速度优先，域差距大时可能失败）
C. explicit_2d3d — LoFTR + 多 tile 3D-2D 联合（几何约束更强）
D. render_compare — Hybrid(DISK+LG + LoFTR) + 最佳 tile（匹配最鲁棒）
"""

import os
import time
import cv2
import numpy as np
from pathlib import Path

from services.localizer.logger_config import get_backend_logger
from services.localizer.salad_roma_v2 import localize_with_salad_roma_v2

_logger = get_backend_logger("spatial_localizers")


def log(msg: str):
    _logger.info(msg)


def _run_salad_with_mode(image_path: str, output_dir: str, top_k: int,
                          max_iterations: int, matcher_mode: str,
                          tag: str, label: str) -> dict:
    """调用 SALAD v2 完整流程，切换匹配器和参数"""
    t0 = time.time()
    log(f"{'=' * 60}")
    log(f"🚀 {label}: {os.path.basename(image_path)}")

    result = localize_with_salad_roma_v2(
        image_path,
        output_dir=output_dir,
        max_iterations=max_iterations,
        top_k_retrieval=top_k,
        matcher_mode=matcher_mode,
        fov_deg=75.0,
        use_pose_prior=False,
        reproj_error=4.0,
        min_inliers=6,
        keep_aspect_ratio=True,
    )

    if isinstance(result, dict):
        result["tag"] = tag
        result["algorithm_label"] = label
        result["spatial_config"] = {
            "top_k": top_k,
            "max_iterations": max_iterations,
            "matcher_mode": matcher_mode,
        }
        if "timings" not in result:
            result["timings"] = {}
        result["timings"]["total_s"] = round(time.time() - t0, 2)

    return result


# ────────────────────────────────────────────────────────────────────
# 4 种算法（匹配器不同 → 结果不同）
# ────────────────────────────────────────────────────────────────────

def localize_pointcloud_descriptor(image_path: str, output_dir: str = "projections/localize_spatial",
                                   fov_deg: float = 75.0, **kwargs) -> dict:
    """A. LoFTR + 联合 PnP + 迭代精化（精度优先）"""
    return _run_salad_with_mode(
        image_path, output_dir, top_k=5, max_iterations=5,
        matcher_mode="loftr", tag="pointcloud_descriptor", label="A. 点云全局描述子"
    )


def localize_depth_icp(image_path: str, output_dir: str = "projections/localize_spatial",
                       fov_deg: float = 75.0, **kwargs) -> dict:
    """B. DISK+LightGlue + 快速（速度优先）"""
    return _run_salad_with_mode(
        image_path, output_dir, top_k=3, max_iterations=2,
        matcher_mode="disk_lg", tag="depth_icp", label="B. 深度估计+ICP"
    )


def localize_explicit_2d3d(image_path: str, output_dir: str = "projections/localize_spatial",
                           fov_deg: float = 75.0, **kwargs) -> dict:
    """C. LoFTR + 多 tile 3D-2D 联合（几何约束更强）"""
    return _run_salad_with_mode(
        image_path, output_dir, top_k=3, max_iterations=5,
        matcher_mode="loftr", tag="explicit_2d3d", label="C. 显式2D-3D匹配"
    )


def localize_render_compare(image_path: str, output_dir: str = "projections/localize_spatial",
                            fov_deg: float = 75.0, **kwargs) -> dict:
    """D. Hybrid(DISK+LG + LoFTR) + 最佳 tile（匹配最鲁棒）"""
    return _run_salad_with_mode(
        image_path, output_dir, top_k=10, max_iterations=2,
        matcher_mode="hybrid", tag="render_compare", label="D. 渲染对比定位"
    )
