"""3D 点云网页显示 API — 网格切片方案。"""

import logging
from pathlib import Path

import numpy as np
from fastapi import APIRouter, Query

logger = logging.getLogger("api.point_cloud")

router = APIRouter(prefix="/api/point-cloud", tags=["point-cloud"])

_DEFAULT_LAS = "projections/downsampled_las/default_2026-05-28-112428_downsampled.las"
_OFFSET_XYZ = [505980.37, 2495851.42, 19.03]
_TILE_SIZE = 50.0  # 网格大小（米）


def _load_las_points(las_path: str):
    """读取 LAS 文件，返回局部坐标 + 颜色。

    注意：LAS 文件已经是局部坐标（octree 构建时已减 offset），无需再减。
    """
    import laspy

    las = laspy.read(str(las_path))
    xs = np.array(las.x)
    ys = np.array(las.y)
    zs = np.array(las.z)

    if hasattr(las, 'red') and hasattr(las, 'green') and hasattr(las, 'blue'):
        r = np.array(las.red).astype(np.float64) / 65535.0
        g = np.array(las.green).astype(np.float64) / 65535.0
        b = np.array(las.blue).astype(np.float64) / 65535.0
        colors = np.column_stack([r, g, b])
    elif hasattr(las, 'intensity'):
        intensity = np.array(las.intensity)
        if intensity.max() > 0:
            intensity = intensity / intensity.max()
        colors = np.column_stack([intensity, intensity, intensity])
    else:
        colors = np.full((len(xs), 3), 0.8)

    points = np.column_stack([xs, ys, zs])
    return points, colors


@router.get("/sampled")
def get_sampled_point_cloud(
    max_points: int = Query(default=100000, ge=100, le=500000),
    las_path: str = Query(default=_DEFAULT_LAS),
):
    """采样点云（用于小数据预览）。"""
    try:
        points, colors = _load_las_points(las_path)
    except Exception as e:
        return {"error": str(e), "points": [], "colors": [], "bounds": {}}

    total = len(points)
    if total > max_points:
        idx = np.random.choice(total, max_points, replace=False)
        points, colors = points[idx], colors[idx]

    return {
        "points": points.tolist(),
        "colors": colors.tolist(),
        "bounds": {"min": points.min(axis=0).tolist(), "max": points.max(axis=0).tolist()},
        "total_points": total,
        "sampled_points": len(points),
    }


@router.get("/tiles")
def list_tiles(
    tile_size: float = Query(default=_TILE_SIZE, gt=0),
    las_path: str = Query(default=_DEFAULT_LAS),
):
    """列出所有网格切片及其元信息。"""
    try:
        points, _ = _load_las_points(las_path)
    except Exception as e:
        return {"error": str(e), "tiles": [], "tile_size": tile_size}

    ix = np.floor(points[:, 0] / tile_size).astype(int)
    iy = np.floor(points[:, 1] / tile_size).astype(int)
    tile_keys, inverse = np.unique(np.column_stack([ix, iy]), axis=0, return_inverse=True)

    tiles = []
    for k, (tix, tiy) in enumerate(tile_keys):
        mask = inverse == k
        cell_points = points[mask]
        tiles.append({
            "ix": int(tix),
            "iy": int(tiy),
            "min": cell_points.min(axis=0).tolist(),
            "max": cell_points.max(axis=0).tolist(),
            "count": int(mask.sum()),
        })

    return {"tile_size": tile_size, "tiles": tiles, "total_points": len(points)}


@router.get("/tile/{ix}/{iy}")
def get_tile(
    ix: int,
    iy: int,
    tile_size: float = Query(default=_TILE_SIZE, gt=0),
    max_points: int = Query(default=50000, ge=100, le=200000),
    las_path: str = Query(default=_DEFAULT_LAS),
):
    """获取指定网格内的点云。"""
    try:
        points, colors = _load_las_points(las_path)
    except Exception as e:
        return {"error": str(e), "points": [], "colors": [], "count": 0}

    ts = tile_size
    mask = (
        (points[:, 0] >= ix * ts) & (points[:, 0] < (ix + 1) * ts) &
        (points[:, 1] >= iy * ts) & (points[:, 1] < (iy + 1) * ts)
    )

    cell_points = points[mask]
    cell_colors = colors[mask]
    total_in_tile = len(cell_points)

    if total_in_tile > max_points:
        idx = np.random.choice(total_in_tile, max_points, replace=False)
        cell_points = cell_points[idx]
        cell_colors = cell_colors[idx]

    return {
        "ix": ix,
        "iy": iy,
        "points": cell_points.tolist(),
        "colors": cell_colors.tolist(),
        "count": len(cell_points),
        "total_in_tile": total_in_tile,
    }
