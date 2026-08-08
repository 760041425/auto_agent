"""3D 点云网页显示 API。"""

import logging
from pathlib import Path

import numpy as np
from fastapi import APIRouter, Query

logger = logging.getLogger("api.point_cloud")

router = APIRouter(prefix="/api/point-cloud", tags=["point-cloud"])

_DEFAULT_LAS = "projections/downsampled_las/default_2026-05-28-112428_downsampled.las"
_OFFSET_XYZ = [505980.37, 2495851.42, 19.03]


@router.get("/sampled")
def get_sampled_point_cloud(
    max_points: int = Query(default=100000, ge=100, le=500000),
    las_path: str = Query(default=_DEFAULT_LAS),
):
    """采样点云并返回 JSON 格式供网页渲染。

    返回:
    {
        "points": [[x, y, z], ...],  # 局部坐标（米）
        "colors": [[r, g, b], ...],  # 0-1 浮点
        "bounds": {"min": [x,y,z], "max": [x,y,z]},
        "total_points": N,
        "sampled_points": M
    }
    """
    try:
        import laspy
    except ImportError:
        return {"error": "laspy not installed", "points": [], "colors": [], "bounds": {}}

    las_file = Path(las_path)
    if not las_file.exists():
        return {"error": f"LAS file not found: {las_path}", "points": [], "colors": [], "bounds": {}}

    try:
        # 读取 LAS
        las = laspy.read(str(las_file))
        total = len(las.x)

        # 采样
        if total > max_points:
            idx = np.random.choice(total, max_points, replace=False)
            idx.sort()
        else:
            idx = np.arange(total)

        # 坐标（转为局部坐标）
        xs = np.array(las.x)[idx] - _OFFSET_XYZ[0]
        ys = np.array(las.y)[idx] - _OFFSET_XYZ[1]
        zs = np.array(las.z)[idx] - _OFFSET_XYZ[2]

        # 颜色
        if hasattr(las, 'red') and hasattr(las, 'green') and hasattr(las, 'blue'):
            # LAS 颜色是 16-bit，转为 0-1 浮点
            r = np.array(las.red)[idx].astype(np.float64) / 65535.0
            g = np.array(las.green)[idx].astype(np.float64) / 65535.0
            b = np.array(las.blue)[idx].astype(np.float64) / 65535.0
            colors = np.column_stack([r, g, b]).tolist()
        elif hasattr(las, 'intensity'):
            # 用强度做灰度
            intensity = np.array(las.intensity)[idx]
            if intensity.max() > 0:
                intensity = intensity / intensity.max()
            colors = np.column_stack([intensity, intensity, intensity]).tolist()
        else:
            # 默认白色
            colors = [[0.8, 0.8, 0.8]] * len(idx)

        points = np.column_stack([xs, ys, zs]).tolist()

        bounds = {
            "min": [float(xs.min()), float(ys.min()), float(zs.min())],
            "max": [float(xs.max()), float(ys.max()), float(zs.max())],
        }

        return {
            "points": points,
            "colors": colors,
            "bounds": bounds,
            "total_points": int(total),
            "sampled_points": len(idx),
        }

    except Exception as e:
        logger.exception("Failed to sample point cloud")
        return {"error": str(e), "points": [], "colors": [], "bounds": {}}
