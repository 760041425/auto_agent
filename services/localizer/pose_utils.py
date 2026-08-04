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
    err_ratio_limit: float = 2.0,
) -> bool:
    """位姿择优：内点数优先（容差 ±inlier_tol），其次重投影误差更小。

    误差约束：当候选误差超过当前误差的 ``err_ratio_limit`` 倍时，
    即使内点更多也不采用。这避免了"联合 PnP 用 37 个额外内点
    把误差从 23px 拉到 104px"之类的劣化选择。
    """
    # 硬约束：误差过大的候选直接拒绝
    if cur_err > 0 and cand_err > cur_err * err_ratio_limit:
        return False
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


# --------------------------------------------------------------------------- #
# 归一化焦距工具
# --------------------------------------------------------------------------- #

def fov_to_normalized_focal(fov_deg: float, img_w: int, img_h: int) -> float:
    """视场角 → 归一化焦距（除以图像宽）。"""
    f = max(img_w, img_h) / (2 * np.tan(np.deg2rad(fov_deg / 2)))
    return f / img_w


def normalized_focal_to_K(normalized_focal: float, img_w: int, img_h: int) -> np.ndarray:
    """归一化焦距 → 相机内参矩阵（主点在图像中心）。"""
    f = float(normalized_focal) * img_w
    return np.array([
        [f, 0, img_w / 2],
        [0, f, img_h / 2],
        [0, 0, 1],
    ], dtype=np.float64)


def extract_normalized_focal(K: np.ndarray, img_w: int) -> float:
    """从相机内参矩阵提取归一化焦距。"""
    return float(K[0, 0]) / float(img_w)


def compute_pnp_score(inlier_count: int, reproj_error_px: float) -> float:
    """综合评分：内点数 / (重投影误差 + eps)，越大越好。"""
    return float(inlier_count) / (float(reproj_error_px) + 1e-6)


# --------------------------------------------------------------------------- #
# 多阶段归一化焦距 PnP 搜索
# --------------------------------------------------------------------------- #

def solve_pnp_with_focal_search(
    object_pts: np.ndarray,
    image_pts: np.ndarray,
    img_w: int,
    img_h: int,
    *,
    initial_K: Optional[np.ndarray] = None,
    fov_deg: float = 75.0,
    search_range: float = 0.3,
    coarse_rounds: int = 3,
    fine_rounds: int = 2,
    splits: int = 5,
    reproj_error: float = 8.0,
    min_inliers: int = 6,
    ransac_method: int = cv2.SOLVEPNP_ITERATIVE,
    confidence: float = 0.85,
    ransac_seed: int = 1337,
    focal_search: bool = True,
) -> dict:
    """多阶段归一化焦距搜索 + PnP，找到最优相机内参估计。

    参考 slam-map ``solve_pnp_with_normalized_focal_search``，适配当前项目：
    通常已有 ``fov_deg`` 或 ``initial_K`` 作为初始估计，搜索范围更小更快。

    参数
    ----------
    initial_K : 外部传入内参，优先使用
    fov_deg : 无 initial_K 时的初始焦距估计
    search_range : 归一化焦距相对搜索范围（±30%）
    coarse_rounds/fine_rounds : 粗/精搜索轮数
    splits : 每轮分段采样数
    focal_search : False 时退化为单次 PnP（用 initial_K 或 fov_deg）

    返回 dict 包含 rvec/tvec/inliers/quality_score/focal_search_summary；
    失败返回 ``{"success": False, "error": "..."}``。
    """
    if object_pts is None or image_pts is None or len(object_pts) < 4:
        return {"success": False, "error": f"点数不足 ({0 if object_pts is None else len(object_pts)})，至少需要 4 个点"}

    # 无焦距搜索时退化为单次 PnP
    if not focal_search:
        K = get_camera_matrix(img_w, img_h, fov_deg=fov_deg, intrinsics=initial_K)
        rvec, tvec, inliers = solve_pnp_ransac(
            object_pts, image_pts, K,
            reproj_error=reproj_error, refine=True,
        )
        if rvec is None:
            return {"success": False, "error": "PnP 无解（单次模式）"}
        return {
            "success": True,
            "rvec": rvec, "tvec": tvec, "inliers": inliers,
            "K": K, "normalized_focal": extract_normalized_focal(K, img_w),
            "focal_search_summary": None,
        }

    # 初始归一化焦距
    if initial_K is not None:
        f_norm_center = extract_normalized_focal(np.asarray(initial_K), img_w)
    else:
        f_norm_center = fov_to_normalized_focal(fov_deg, img_w, img_h)

    f_norm_min = f_norm_center * (1 - search_range)
    f_norm_max = f_norm_center * (1 + search_range)

    best: Optional[dict] = None
    total_attempts = 0
    total_success = 0

    def _try_focal(f_norm: float, seed_offset: int) -> Optional[dict]:
        nonlocal total_attempts, total_success
        total_attempts += 1
        K_i = normalized_focal_to_K(f_norm, img_w, img_h)
        rvec, tvec, inliers = solve_pnp_ransac(
            object_pts, image_pts, K_i,
            reproj_error=reproj_error, refine=True,
            method=ransac_method,
            iterations=2000,
            confidence=confidence,
        )
        if rvec is None:
            return None
        ic = len(inliers) if inliers is not None else len(object_pts)
        if ic < min_inliers:
            return None
        total_success += 1
        err = compute_reprojection_error(rvec, tvec, K_i, object_pts, image_pts)
        score = compute_pnp_score(ic, err)
        return {
            "rvec": rvec, "tvec": tvec, "inliers": inliers,
            "K": K_i, "normalized_focal": f_norm,
            "inlier_count": ic, "reproj_error_px": err, "score": score,
        }

    def _pick_best(candidates: list) -> Optional[dict]:
        if not candidates:
            return None
        return max(candidates, key=lambda c: (c["score"], c["inlier_count"], -c["reproj_error_px"]))

    # --- 粗搜阶段：在 [f_norm_min, f_norm_max] 区间均匀采样 ---
    current_min, current_max = f_norm_min, f_norm_center * (1 + search_range)
    current_min, current_max = min(current_min, current_max), max(current_min, current_max)
    seed_base = ransac_seed

    for round_idx in range(coarse_rounds):
        step = (current_max - current_min) / max(splits - 1, 1)
        candidates_fnorm = [current_min + step * i for i in range(splits)]
        round_results = []
        for i, fn in enumerate(candidates_fnorm):
            r = _try_focal(fn, seed_base + round_idx * 1000 + i)
            if r is not None:
                round_results.append(r)
        round_best = _pick_best(round_results)
        if round_best is not None:
            # 收缩区间到最优值附近
            shrink = (current_max - current_min) / splits
            current_min = max(f_norm_min, round_best["normalized_focal"] - shrink)
            current_max = min(f_norm_max, round_best["normalized_focal"] + shrink)
            if best is None or round_best["score"] > best["score"]:
                best = round_best

    # --- 精搜阶段：步长收紧到 ±0.01 ---
    fine_step = 0.01
    for round_idx in range(fine_rounds):
        if best is None:
            break
        center = best["normalized_focal"]
        candidates_fnorm = [center - fine_step, center, center + fine_step]
        round_results = []
        for i, fn in enumerate(candidates_fnorm):
            r = _try_focal(fn, seed_base + 100000 + round_idx * 1000 + i)
            if r is not None:
                round_results.append(r)
        round_best = _pick_best(round_results)
        if round_best is not None and (best is None or round_best["score"] > best["score"]):
            best = round_best

    if best is None:
        return {"success": False, "error": f"所有焦距候选 PnP 失败（尝试 {total_attempts} 次）"}

    summary = {
        "attempts": total_attempts,
        "success": total_success,
        "best_normalized_focal": best["normalized_focal"],
        "best_score": best["score"],
    }
    solve_pnp_with_focal_search.last_summary = summary
    return {
        "success": True,
        "rvec": best["rvec"],
        "tvec": best["tvec"],
        "inliers": best["inliers"],
        "K": best["K"],
        "normalized_focal": best["normalized_focal"],
        "inlier_count": best["inlier_count"],
        "reproj_error_px": best["reproj_error_px"],
        "score": best["score"],
        "focal_search_summary": summary,
    }


solve_pnp_with_focal_search.last_summary = None


# --------------------------------------------------------------------------- #
# PnP 质量门控
# --------------------------------------------------------------------------- #

def annotate_pnp_quality(
    pnp_result: dict,
    min_score: float = 4.0,
    min_inliers: int = 6,
    max_reproj_error_px: float = 8.0,
) -> dict:
    """为 PnP 结果补充质量门控标注。

    多维度门控：综合评分、最少内点、最大重投影误差。
    返回结果增加 ``quality_passed`` / ``quality_reasons`` / ``quality_score``。
    """
    if not isinstance(pnp_result, dict) or not pnp_result.get("success"):
        if isinstance(pnp_result, dict):
            pnp_result["quality_passed"] = False
            pnp_result["quality_reasons"] = ["pnp_failed"]
            pnp_result["quality_score"] = 0.0
        return pnp_result

    score = float(pnp_result.get("score") or compute_pnp_score(
        int(pnp_result.get("inlier_count", 0)),
        float(pnp_result.get("reproj_error_px") or pnp_result.get("reprojection_error", 0)),
    ))
    inliers = int(pnp_result.get("inlier_count", 0))
    reproj = float(pnp_result.get("reproj_error_px") or pnp_result.get("reprojection_error", 0))

    reasons = []
    if score < min_score:
        reasons.append(f"score<{min_score}")
    if inliers < min_inliers:
        reasons.append(f"inliers<{min_inliers}")
    if reproj > max_reproj_error_px:
        reasons.append(f"reproj_error>{max_reproj_error_px}px")

    pnp_result["quality_score"] = score
    pnp_result["quality_passed"] = len(reasons) == 0
    pnp_result["quality_reasons"] = reasons
    return pnp_result
