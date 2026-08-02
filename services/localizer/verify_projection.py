"""2D 单应拟合诊断及迁入本上下文的坐标转换能力。

本地函数的单应矩阵和待比较像素来自同一批匹配，3D 坐标也来自同一张
NPY 坐标图。该路径只能输出像素级拟合诊断，不能输出可用的米制一致性；
独立位姿精度必须由 benchmark 的 holdout ground truth 提供。
"""

import json
import logging
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np

_logger = logging.getLogger("localizer.verify_projection")


def load_published_tile_images(
    tile_index_path: str | Path = "projections/tile_index.json",
    *,
    require_existing: bool = True,
) -> list[str]:
    """读取当前发布的 MapTile 图像，不扫描磁盘中的历史实验资产。"""
    path = Path(tile_index_path)
    with path.open() as stream:
        records = json.load(stream)

    images: list[str] = []
    for record in records:
        image_path = record.get("image_path") or record.get("path")
        if not record.get("accepted", True) or not image_path:
            continue
        if require_existing and not Path(image_path).is_file():
            continue
        images.append(str(image_path))
    return images


# --------------------------------------------------------------------------- #
# 本地 homography 验证
# --------------------------------------------------------------------------- #

def compute_homography(
    pts_query: np.ndarray,
    pts_tile: np.ndarray,
    reproj_thresh: float = 5.0,
) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """计算 query ↔ tile 的单应性矩阵。

    参数
    ----------
    pts_query : (N, 2) query 图像素坐标
    pts_tile : (N, 2) tile 图像素坐标
    reproj_thresh : RANSAC 重投影阈值（像素）

    返回
    -------
    (H, mask) — H 是 3×3 单应矩阵（query → tile），mask 是内点掩码
    """
    if len(pts_query) < 4 or len(pts_query) != len(pts_tile):
        return None, None
    pts_q = np.asarray(pts_query, dtype=np.float32).reshape(-1, 1, 2)
    pts_t = np.asarray(pts_tile, dtype=np.float32).reshape(-1, 1, 2)
    H, mask = cv2.findHomography(pts_q, pts_t, cv2.RANSAC, reproj_thresh)
    return H, mask


def verify_projection_local(
    query_img: np.ndarray,
    tile_img: np.ndarray,
    tile_npy: np.ndarray,
    pts_query: np.ndarray,
    pts_tile: np.ndarray,
    n_samples: int = 50,
    reproj_thresh: float = 5.0,
) -> dict:
    """输出同一匹配集上的 homography 像素拟合诊断。

    参数
    ----------
    query_img : (H, W, 3) query 图像
    tile_img : (H, W, 3) tile 图像
    tile_npy : (H, W, 3) tile 的 NPY 坐标图
    pts_query : (N, 2) query 匹配点像素坐标
    pts_tile : (N, 2) tile 匹配点像素坐标
    n_samples : 采样验证点数

    ``tile_npy`` 仅用于明确数据来源；因为没有第二个独立 3D reference，
    米制字段固定为 ``None``，不得再把同源坐标差解释为验证结果。
    """
    del query_img, tile_img, tile_npy, n_samples

    base_result = {
        "metric_type": "projection_consistency",
        "absolute_accuracy": {
            "status": "not_available",
            "reason": "independent ground truth not provided",
        },
    }

    pts_query = np.asarray(pts_query, dtype=np.float32).reshape(-1, 2)
    pts_tile = np.asarray(pts_tile, dtype=np.float32).reshape(-1, 2)
    H, mask = compute_homography(pts_query, pts_tile, reproj_thresh)
    if H is None:
        return {
            **base_result,
            "success": False,
            "status": "not_available",
            "error": "homography failed",
        }

    inlier_mask = mask.ravel() > 0
    n_inliers = int(inlier_mask.sum())
    predicted = cv2.perspectiveTransform(pts_query.reshape(-1, 1, 2), H).reshape(-1, 2)
    residuals = np.linalg.norm(predicted - pts_tile, axis=1)
    inlier_residuals = residuals[inlier_mask] if n_inliers else np.array([], dtype=np.float32)
    fit = {
        "status": "available",
        "n_matches": int(len(pts_query)),
        "n_inliers": n_inliers,
        "inlier_ratio": round(float(n_inliers / len(pts_query)), 4) if len(pts_query) else 0.0,
        "inlier_median_residual_px": (
            round(float(np.median(inlier_residuals)), 3) if len(inlier_residuals) else None
        ),
        "all_match_max_residual_px": (
            round(float(np.max(residuals)), 3) if len(residuals) else None
        ),
    }
    return {
        **base_result,
        "success": True,
        "status": "not_available",
        "reason": "same_source_npy_is_not_independent_validation",
        "source": "same_tile_npy",
        "n_matches": len(pts_query),
        "n_inliers": n_inliers,
        "homography": H.tolist(),
        "homography_fit": fit,
        "samples": [],
        "mean_m": None,
        "median_m": None,
        "max_m": None,
        "mean_error_m": None,
        "median_error_m": None,
        "max_error_m": None,
        "note": "same-source NPY comparison is circular; meter validation suppressed",
    }


# --------------------------------------------------------------------------- #
# 本地坐标转换（从 slam-map 迁移，不依赖外部服务）
# --------------------------------------------------------------------------- #

def build_projection_xyz_map(
    points_world: np.ndarray,
    rvec: np.ndarray,
    tvec: np.ndarray,
    camera_matrix: np.ndarray,
    *,
    width: int,
    height: int,
    splat_radius: int = 1,
) -> np.ndarray:
    """按最终 PnP 位姿生成与查询图对齐的 XYZ 图，近点遮挡远点。"""
    xyz_map = np.zeros((height, width, 3), dtype=np.float32)
    points = np.asarray(points_world, dtype=np.float64).reshape(-1, 3)
    if not len(points):
        return xyz_map

    rotation = cv2.Rodrigues(np.asarray(rvec, dtype=np.float64).reshape(3, 1))[0]
    translation = np.asarray(tvec, dtype=np.float64).reshape(1, 3)
    camera_points = (rotation @ points.T).T + translation
    depth = camera_points[:, 2]
    valid_depth = np.isfinite(depth) & (depth > 0)
    if not np.any(valid_depth):
        return xyz_map

    source_indices = np.flatnonzero(valid_depth)
    camera_points = camera_points[valid_depth]
    projected = (np.asarray(camera_matrix, dtype=np.float64) @ camera_points.T).T
    projected_xy = projected[:, :2] / projected[:, 2:3]
    base_x = np.rint(projected_xy[:, 0]).astype(np.int64)
    base_y = np.rint(projected_xy[:, 1]).astype(np.int64)
    base_depth = camera_points[:, 2]

    pixels = []
    for dy in range(-splat_radius, splat_radius + 1):
        for dx in range(-splat_radius, splat_radius + 1):
            x = base_x + dx
            y = base_y + dy
            inside = (x >= 0) & (x < width) & (y >= 0) & (y < height)
            if np.any(inside):
                pixels.append(
                    (
                        y[inside] * width + x[inside],
                        base_depth[inside],
                        source_indices[inside],
                    )
                )
    if not pixels:
        return xyz_map

    flat_pixels = np.concatenate([item[0] for item in pixels])
    depths = np.concatenate([item[1] for item in pixels])
    sources = np.concatenate([item[2] for item in pixels])
    order = np.lexsort((depths, flat_pixels))
    sorted_pixels = flat_pixels[order]
    first = np.r_[True, sorted_pixels[1:] != sorted_pixels[:-1]]
    chosen = order[first]
    xyz_map.reshape(-1, 3)[flat_pixels[chosen]] = points[sources[chosen]]
    return xyz_map


def build_local_coordinate_transform_context(
    query_points: np.ndarray,
    world_points: np.ndarray,
    projection_xyz: np.ndarray,
    output_path: str | Path,
    *,
    reproj_thresh_m: float = 3.0,
    consistency_threshold_m: float = 0.3,
    consistency_sample_limit: int = 256,
) -> dict:
    """拟合 query 像素→SLAM XY 单应矩阵并保存最终位姿 XYZ NPY。"""
    query = np.asarray(query_points, dtype=np.float32).reshape(-1, 2)
    world = np.asarray(world_points, dtype=np.float32).reshape(-1, 3)
    if len(query) < 4 or len(query) != len(world):
        return {"status": "not_available", "reason": "insufficient_2d_3d_points"}

    homography, mask = cv2.findHomography(
        query.reshape(-1, 1, 2),
        world[:, :2].reshape(-1, 1, 2),
        cv2.RANSAC,
        reproj_thresh_m,
    )
    if homography is None or not np.all(np.isfinite(homography)):
        return {"status": "not_available", "reason": "world_homography_failed"}

    xyz = np.asarray(projection_xyz, dtype=np.float32)
    if xyz.ndim != 3 or xyz.shape[2] != 3:
        return {"status": "not_available", "reason": "projection_xyz_shape_invalid"}

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.save(path, xyz)
    try:
        stored_path = path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        stored_path = str(path.resolve())
    context = {
        "status": "ready",
        "source": "local_final_pose",
        "homography": homography.tolist(),
        "projection_npy": stored_path,
        "width": int(xyz.shape[1]),
        "height": int(xyz.shape[0]),
        "n_matches": int(len(query)),
        "n_inliers": int(mask.sum()) if mask is not None else int(len(query)),
    }
    context["consistency"] = evaluate_local_coordinate_consistency(
        context,
        threshold_m=consistency_threshold_m,
        sample_limit=consistency_sample_limit,
    )
    return context


def evaluate_local_coordinate_consistency(
    context: dict,
    *,
    threshold_m: float = 0.3,
    sample_limit: int = 256,
    min_samples: int = 4,
) -> dict:
    """以多点 H→SLAM XYZ 与 NPY XYZ 的三维距离中位数作定位可信判据。

    单应矩阵 H 把像素映射到 SLAM 地面平面（Z=0），NPY 包含真实高度。
    比较时把 H→SLAM 的 Z 设为 0，与 NPY 的 XYZ 计算三维欧氏距离，
    因此 XY 偏移和高度偏差都能被反映到中位差中。
    """
    base = {
        "status": "not_available",
        "decision_metric": "median_3d_difference_m",
        "threshold_m": float(threshold_m),
        "sample_count": 0,
        "median_m": None,
        "p95_m": None,
        "max_m": None,
        "passed": False,
    }
    if not isinstance(context, dict) or context.get("status") != "ready":
        return {**base, "reason": "coordinate_transform_context_not_ready"}
    try:
        homography = np.asarray(context["homography"], dtype=np.float64).reshape(3, 3)
        width = int(context["width"])
        height = int(context["height"])
        path = Path(context["projection_npy"])
        if not path.is_absolute():
            path = Path.cwd() / path
        projection_xyz = np.load(path, mmap_mode="r")
    except (KeyError, OSError, TypeError, ValueError):
        return {**base, "reason": "coordinate_transform_artifact_unavailable"}
    if projection_xyz.shape != (height, width, 3):
        return {**base, "reason": "projection_npy_shape_mismatch"}

    finite = np.all(np.isfinite(projection_xyz), axis=2)
    nonzero = np.any(np.asarray(projection_xyz) != 0, axis=2)
    valid_pixels = np.argwhere(finite & nonzero)
    if len(valid_pixels) < min_samples:
        return {
            **base,
            "sample_count": int(len(valid_pixels)),
            "reason": "insufficient_valid_projection_pixels",
        }
    if len(valid_pixels) > sample_limit:
        selected = np.linspace(0, len(valid_pixels) - 1, sample_limit, dtype=np.int64)
        valid_pixels = valid_pixels[selected]

    pixel_y = valid_pixels[:, 0].astype(np.float64)
    pixel_x = valid_pixels[:, 1].astype(np.float64)
    homogeneous = np.column_stack([pixel_x, pixel_y, np.ones(len(pixel_x))])
    mapped = (homography @ homogeneous.T).T
    valid_h = np.all(np.isfinite(mapped), axis=1) & (np.abs(mapped[:, 2]) > 1e-12)
    if int(valid_h.sum()) < min_samples:
        return {
            **base,
            "sample_count": int(valid_h.sum()),
            "reason": "insufficient_valid_homography_samples",
        }

    # H 把像素映射到 SLAM 地面平面（Z=0），NPY 包含真实高度。
    # 比较三维欧氏距离：slam_xyz = (slam_x, slam_y, 0)，npy_xyz = NPY XYZ。
    mapped_xy = mapped[valid_h, :2] / mapped[valid_h, 2:3]
    slam_xyz = np.column_stack([mapped_xy, np.zeros(len(mapped_xy), dtype=np.float64)])
    npy_xyz = np.asarray(
        projection_xyz[valid_pixels[valid_h, 0], valid_pixels[valid_h, 1]],
        dtype=np.float64,
    )
    distances = np.linalg.norm(slam_xyz - npy_xyz, axis=1)
    median = float(np.median(distances))
    return {
        **base,
        "status": "available",
        "sample_count": int(len(distances)),
        "mean_m": round(float(np.mean(distances)), 3),
        "median_m": round(median, 3),
        "p95_m": round(float(np.percentile(distances, 95)), 3),
        "max_m": round(float(np.max(distances)), 3),
        "passed": bool(median < threshold_m),
        "reason": None,
    }


def query_local_coordinate_transform(context: dict, *, u: float, v: float) -> dict:
    """在本地任务产物上执行单点 H→SLAM / NPY XYZ 坐标交叉验证。"""
    base = {
        "status": "not_available",
        "validation_type": "local_coordinate_crosscheck",
        "absolute_accuracy": False,
        "u": float(u),
        "v": float(v),
        "pixel_to_slam": None,
        "npy_point": None,
        "difference_m": None,
        "note": "local homography/NPY consistency; not independent pose accuracy",
    }
    if not isinstance(context, dict) or context.get("status") != "ready":
        return {**base, "reason": "coordinate_transform_context_not_ready"}

    try:
        homography = np.asarray(context["homography"], dtype=np.float64).reshape(3, 3)
        width = int(context["width"])
        height = int(context["height"])
        path = Path(context["projection_npy"])
        if not path.is_absolute():
            path = Path.cwd() / path
        projection_xyz = np.load(path, mmap_mode="r")
    except (KeyError, OSError, TypeError, ValueError):
        return {**base, "reason": "coordinate_transform_artifact_unavailable"}

    if projection_xyz.shape != (height, width, 3):
        return {**base, "reason": "projection_npy_shape_mismatch"}

    pixel_x = float(u) * (width - 1)
    pixel_y = float(v) * (height - 1)
    mapped = homography @ np.array([pixel_x, pixel_y, 1.0])
    if not np.all(np.isfinite(mapped)) or abs(mapped[2]) < 1e-12:
        return {**base, "reason": "homography_projection_invalid"}
    slam_x, slam_y = mapped[:2] / mapped[2]
    pixel_to_slam = {
        "slam_x": round(float(slam_x), 3),
        "slam_y": round(float(slam_y), 3),
        "slam_z": 0.0,
    }

    npy_xyz = np.asarray(
        projection_xyz[int(pixel_y), int(pixel_x)], dtype=np.float64
    )
    if not np.all(np.isfinite(npy_xyz)) or np.allclose(npy_xyz, 0.0):
        return {
            **base,
            "pixel_to_slam": pixel_to_slam,
            "reason": "projection_npy_pixel_invalid",
        }
    npy_point = {
        "x": round(float(npy_xyz[0]), 3),
        "y": round(float(npy_xyz[1]), 3),
        "z": round(float(npy_xyz[2]), 3),
    }
    slam_xyz = np.array(
        [pixel_to_slam["slam_x"], pixel_to_slam["slam_y"], pixel_to_slam["slam_z"]]
    )
    return {
        **base,
        "status": "available",
        "pixel_to_slam": pixel_to_slam,
        "npy_point": npy_point,
        "difference_m": round(float(np.linalg.norm(slam_xyz - npy_xyz)), 3),
        "reason": None,
    }
