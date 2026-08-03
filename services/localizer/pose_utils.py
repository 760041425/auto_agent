"""
共享几何工具函数 — 供 salad_roma_v2 / 未来 v3 复用

消除 services/localizer/__init__.py 与 salad_roma.py 之间的重复代码，
提供数值稳定的旋转表示转换、PnP 求解（含 LO-RANSAC 细化）、
2D-2D 几何验证、以及 LAS 点云验证。

修复的关键问题：
1. 原 _rotation_matrix_to_quaternion 直接除 q[0]，180° 旋转时数值崩溃 → 改用 Shepperd 最大迹分支
2. 原 PnP reprojErr=8px 过松 → 默认 4px + 可选 solvePnPRefineLM 细化
3. 原流程无 2D-2D 几何预过滤 → 新增 E-matrix RANSAC
4. 原无 LAS 验证 → 新增 kdtree 近邻验证（从 matcher/__init__.py 重构）
"""

import logging
from typing import Optional, Tuple

import cv2
import numpy as np

_logger = logging.getLogger("localizer.pose_utils")


# --------------------------------------------------------------------------- #
# 旋转 ↔ 四元数（数值稳定）
# --------------------------------------------------------------------------- #

def rotation_matrix_to_quaternion(R: np.ndarray) -> np.ndarray:
    """旋转矩阵 → 单位四元数 [w, x, y, z]（Shepperd 最大迹分支）。

    原实现（__init__.py:222 / salad_roma.py:793）直接除 ``q[0]``，
    当旋转接近 180° 时 ``q[0] ≈ 0`` 导致数值爆炸。
    本实现选择 trace/对角元最大者作为除数，避免该问题。
    """
    R = np.asarray(R, dtype=np.float64)
    trace = R[0, 0] + R[1, 1] + R[2, 2]

    if trace > 0:
        s = 0.5 / np.sqrt(trace + 1.0)
        w = 0.25 / s
        x = (R[2, 1] - R[1, 2]) * s
        y = (R[0, 2] - R[2, 0]) * s
        z = (R[1, 0] - R[0, 1]) * s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = 2.0 * np.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2])
        w = (R[2, 1] - R[1,2]) / s
        x = 0.25 * s
        y = (R[0, 1] + R[1, 0]) / s
        z = (R[0, 2] + R[2, 0]) / s
    elif R[1, 1] > R[2, 2]:
        s = 2.0 * np.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2])
        w = (R[0, 2] - R[2, 0]) / s
        x = (R[0, 1] + R[1, 0]) / s
        y = 0.25 * s
        z = (R[1, 2] + R[2, 1]) / s
    else:
        s = 2.0 * np.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1])
        w = (R[1, 0] - R[0, 1]) / s
        x = (R[0, 2] + R[2, 0]) / s
        y = (R[1, 2] + R[2, 1]) / s
        z = 0.25 * s

    q = np.array([w, x, y, z], dtype=np.float64)
    norm = np.linalg.norm(q)
    if norm < 1e-10:
        return np.array([1.0, 0.0, 0.0, 0.0])
    return q / norm


def quaternion_to_rotation_matrix(q: np.ndarray) -> np.ndarray:
    """单位四元数 [w, x, y, z] → 3×3 旋转矩阵。"""
    q = np.asarray(q, dtype=np.float64)
    q = q / np.linalg.norm(q)
    w, x, y, z = q
    return np.array([
        [1 - 2*(y*y + z*z),   2*(x*y - w*z),   2*(x*z + w*y)],
        [2*(x*y + w*z),   1 - 2*(x*x + z*z),   2*(y*z - w*x)],
        [2*(x*z - w*y),   2*(y*z + w*x),   1 - 2*(x*x + y*y)],
    ], dtype=np.float64)


# --------------------------------------------------------------------------- #
# 相机内参
# --------------------------------------------------------------------------- #

def get_camera_matrix(
    img_w: int,
    img_h: int,
    fov_deg: float = 75.0,
    intrinsics: Optional[np.ndarray] = None,
) -> np.ndarray:
    """构造相机内参矩阵。

    优先使用外部传入的 ``intrinsics``（3×3）；否则按 ``fov_deg`` 估计。
    原实现硬编码 ``fov_deg`` 导致与真实相机不一致。
    """
    if intrinsics is not None:
        K = np.asarray(intrinsics, dtype=np.float64).reshape(3, 3)
        return K
    f = max(img_w, img_h) / (2 * np.tan(np.deg2rad(fov_deg / 2)))
    return np.array([
        [f, 0, img_w / 2],
        [0, f, img_h / 2],
        [0, 0, 1],
    ], dtype=np.float64)


# --------------------------------------------------------------------------- #
# PnP 求解（含可选 LO-RANSAC 细化）
# --------------------------------------------------------------------------- #

def solve_pnp_ransac(
    object_pts: np.ndarray,
    image_pts: np.ndarray,
    camera_matrix: np.ndarray,
    dist_coeffs: Optional[np.ndarray] = None,
    method: int = cv2.SOLVEPNP_ITERATIVE,
    reproj_error: float = 4.0,
    iterations: int = 2000,
    confidence: float = 0.999,
    refine: bool = True,
) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], Optional[np.ndarray]]:
    """PnP RANSAC 求解，可选 Levenberg-Marquardt 细化。

    原实现 ``reprojErr=8.0`` 过松，且无细化步骤。
    """
    if object_pts is None or image_pts is None or len(object_pts) < 4:
        return None, None, None

    obj = np.asarray(object_pts, dtype=np.float64).reshape(-1, 3)
    img = np.asarray(image_pts, dtype=np.float64).reshape(-1, 2)
    if len(obj) < 4 or len(obj) != len(img):
        return None, None, None

    if dist_coeffs is None:
        dist_coeffs = np.zeros((4, 1), dtype=np.float64)

    success, rvec, tvec, inliers = cv2.solvePnPRansac(
        obj, img, camera_matrix, dist_coeffs,
        iterationsCount=iterations,
        reprojectionError=reproj_error,
        confidence=confidence,
        flags=method,
    )
    if not success:
        return None, None, None

    if inliers is not None and len(inliers.ravel()) >= 4 and refine:
        try:
            rvec, tvec = cv2.solvePnPRefineLM(
                obj[inliers.ravel()], img[inliers.ravel()],
                camera_matrix, dist_coeffs, rvec, tvec,
            )
        except cv2.error as e:
            _logger.debug(f"solvePnPRefineLM failed, keeping RANSAC result: {e}")

    return rvec, tvec, inliers


# --------------------------------------------------------------------------- #
# 重投影误差 / 位姿比较
# --------------------------------------------------------------------------- #

def compute_reprojection_error(
    rvec: np.ndarray,
    tvec: np.ndarray,
    camera_matrix: np.ndarray,
    object_pts: np.ndarray,
    image_pts: np.ndarray,
) -> float:
    """平均重投影误差（像素）。"""
    if object_pts is None or image_pts is None or len(object_pts) == 0:
        return float("inf")
    proj, _ = cv2.projectPoints(
        np.asarray(object_pts, dtype=np.float64),
        rvec, tvec, camera_matrix, None,
    )
    proj = proj.reshape(-1, 2)
    tgt = np.asarray(image_pts, dtype=np.float64).reshape(-1, 2)
    if len(proj) != len(tgt):
        return float("inf")
    return float(np.sqrt(np.sum((proj - tgt) ** 2, axis=1)).mean())


def is_pose_better(
    cand_inliers: int, cand_err: float,
    cur_inliers: int, cur_err: float,
    inlier_tol: int = 2,
) -> bool:
    """位姿择优：内点数优先（容差 ±inlier_tol），其次重投影误差更小。"""
    if cand_inliers > cur_inliers + inlier_tol:
        return True
    if abs(cand_inliers - cur_inliers) <= inlier_tol and cand_err < cur_err:
        return True
    return False


# --------------------------------------------------------------------------- #
# 2D-2D 几何预过滤（E-matrix RANSAC）
# --------------------------------------------------------------------------- #

def verify_essential_matrix(
    pts1: np.ndarray,
    pts2: np.ndarray,
    camera_matrix: np.ndarray,
    threshold: float = 1.0,
    confidence: float = 0.999,
) -> Tuple[Optional[np.ndarray], int]:
    """通过本质矩阵 RANSAC 验证 2D 对应的几何一致性。

    在 PnP 之前剔除误匹配，减少外点对位姿估计的干扰。
    返回 ``(mask, n_inliers)``；失败时 mask 为 None。
    """
    if pts1 is None or pts2 is None or len(pts1) < 5:
        return None, 0
    p1 = np.asarray(pts1, dtype=np.float64).reshape(-1, 2)
    p2 = np.asarray(pts2, dtype=np.float64).reshape(-1, 2)
    if len(p1) != len(p2) or len(p1) < 5:
        return None, 0

    try:
        E, mask = cv2.findEssentialMat(
            p1, p2, camera_matrix,
            method=cv2.RANSAC, prob=confidence, threshold=threshold,
        )
        if mask is None:
            return None, 0
        return mask, int(mask.ravel().sum())
    except cv2.error as e:
        _logger.debug(f"findEssentialMat failed: {e}")
        return None, 0


# --------------------------------------------------------------------------- #
# LAS 点云 3D 验证
# --------------------------------------------------------------------------- #

def verify_with_las_points(
    matched_coords_3d: np.ndarray,
    las_kdtree,
    tol: float = 3.0,
) -> dict:
    """对 3D 匹配点做 LAS 最近邻验证。

    参数
    ----------
    matched_coords_3d : (N, 3) 匹配得到的世界坐标
    las_kdtree : scipy.spatial.cKDTree，由 load_colmap 建立的 _POINT_INDEX["tree"]
    tol : 接受阈值（米）
    """
    empty = {"total": 0, "verified": 0, "verification_rate": 0.0,
             "mean_distance_m": float("inf"), "details": []}
    if matched_coords_3d is None or len(matched_coords_3d) == 0:
        return empty
    pts = np.asarray(matched_coords_3d, dtype=np.float64).reshape(-1, 3)
    pts = pts[np.isfinite(pts).all(axis=1)]
    if len(pts) == 0:
        return empty

    try:
        dists, _ = las_kdtree.query(pts)
    except Exception as e:
        _logger.debug(f"LAS verification query failed: {e}")
        return empty

    n_ok = int((dists <= tol).sum())
    return {
        "total": int(len(pts)),
        "verified": n_ok,
        "verification_rate": float(n_ok / len(pts)),
        "mean_distance_m": float(dists.mean()),
        "details": [],
    }


# --------------------------------------------------------------------------- #
# 图像 resize（保持宽高比）
# --------------------------------------------------------------------------- #

def resize_keep_aspect(image: np.ndarray, target_size: int = 512) -> Tuple[np.ndarray, float, Tuple[int, int]]:
    """保持宽高比缩放到 target_size × target_size，不足处填 0。

    解决原实现强制 resize 512×512 导致的比例失真。
    返回 ``(padded_image, scale, (pad_x, pad_y))``，便于后续把像素坐标映射回原图。
    """
    if image is None:
        raise ValueError("image is None")
    h, w = image.shape[:2]
    if h == 0 or w == 0:
        raise ValueError("image has zero dimension")
    scale = target_size / max(h, w)
    new_w = int(round(w * scale))
    new_h = int(round(h * scale))
    resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

    pad_x = (target_size - new_w) // 2
    pad_y = (target_size - new_h) // 2
    if len(resized.shape) == 3:
        out = np.zeros((target_size, target_size, resized.shape[2]), dtype=resized.dtype)
    else:
        out = np.zeros((target_size, target_size), dtype=resized.dtype)
    out[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized
    return out, scale, (pad_x, pad_y)


def map_coords_to_original(
    pts: np.ndarray, scale: float, pad: Tuple[int, int]
) -> np.ndarray:
    """把 resize 后的像素坐标映射回原始图像坐标。"""
    pts = np.asarray(pts, dtype=np.float64).reshape(-1, 2).copy()
    pts[:, 0] = (pts[:, 0] - pad[0]) / scale
    pts[:, 1] = (pts[:, 1] - pad[1]) / scale
    return pts


# --------------------------------------------------------------------------- #
# 自适应早停
# --------------------------------------------------------------------------- #

def adaptive_early_stop(
    round_results: list, patience: int = 1, min_improvement: float = 0.05,
) -> bool:
    """自适应早停：连续 ``patience`` 轮无显著提升则停止。

    ``round_results`` 是每轮的最佳误差列表（越小越好）。
    """
    if len(round_results) < patience + 1:
        return False
    best_so_far = min(round_results[:-patience])
    recent = round_results[-patience:]
    for v in recent:
        if best_so_far == 0 or (best_so_far - v) / max(best_so_far, 1e-6) > min_improvement:
            return False
    return True
