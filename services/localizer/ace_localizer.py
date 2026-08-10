"""
ACE 场景坐标回归定位 — 三种实现

1. ace_with_normal     — 估计法线图 + 6ch ACE（需要法线估计）
2. ace_rgb_only        — 修改模型为 3ch RGB（快速适配）
3. depth_icp           — 深度估计 + ICP 配准
"""

import os
import time
import cv2
import numpy as np
import torch

from services.localizer.logger_config import get_backend_logger
from services.localizer.pose_utils import (
    get_camera_matrix, solve_pnp_with_focal_search,
    annotate_pnp_quality, rotation_matrix_to_quaternion,
    verify_with_las_points,
)

_logger = get_backend_logger("ace_localizer")


def log(msg: str):
    _logger.info(msg)


# ────────────────────────────────────────────────────────────────────
# 1. ACE + 法线估计（6ch 输入）
# ────────────────────────────────────────────────────────────────────

def _estimate_normal_simple(rgb_image):
    """简单法线估计：从 RGB 梯度近似（快速但粗糙）"""
    gray = cv2.cvtColor(rgb_image, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0

    # Sobel 梯度
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)

    # 法线 = (-gx, -gy, 1) normalized
    normal = np.stack([-gx, -gy, np.ones_like(gray)], axis=-1)
    norm = np.linalg.norm(normal, axis=-1, keepdims=True)
    normal = normal / (norm + 1e-8)

    # 映射到 [0, 1]
    normal = (normal + 1.0) * 0.5
    return normal.astype(np.float32)


def ace_with_normal(image_path: str, output_dir: str = "projections/localize_ace",
                    fov_deg: float = 75.0, **kwargs) -> dict:
    """ACE 定位 — 估计法线 + 6ch 输入"""
    tag = "ace_normal"
    t0 = time.time()
    log(f"{'=' * 60}")
    log(f"🚀 ACE（法线估计）定位: {os.path.basename(image_path)}")

    from services.localizer.coord_regression import load_coord_regression, predict_dense

    model = load_coord_regression()

    query_img = cv2.imread(image_path)
    if query_img is None:
        return {"success": False, "error": "Cannot read query image", "tag": tag}

    # 估计法线图
    normal_map = _estimate_normal_simple(query_img)
    h, w = query_img.shape[:2]
    normal_map = cv2.resize(normal_map, (w, h), interpolation=cv2.INTER_LINEAR)

    # ACE 预测（返回 tuple: pts_2d, pts_3d, confidence）
    with torch.no_grad():
        pts_2d, pts_3d, confidence = predict_dense(model, query_img, normal_map=normal_map)

    if len(pts_3d) < 6:
        return {"success": False, "error": "ACE 预测点不足", "tag": tag, "elapsed": round(time.time() - t0, 2)}

    if len(pts_3d) < 6:
        return {"success": False, "error": "ACE 预测点不足", "tag": tag, "elapsed": round(time.time() - t0, 2)}

    # 过滤低置信度
    mask = confidence > 0.3
    pts_3d, pts_2d = pts_3d[mask], pts_2d[mask]

    if len(pts_3d) < 6:
        pts_3d, pts_2d = result['coords_3d'], result['points_2d']

    # PnP 求解
    h_q, w_q = query_img.shape[:2]
    K = get_camera_matrix(w_q, h_q, fov_deg=fov_deg)

    # 降采样 + 宽松阈值（ACE 预测坐标不够精确）
    step = max(1, len(pts_3d) // 400)
    pnp_out = solve_pnp_with_focal_search(
        pts_3d[::step], pts_2d[::step], w_q, h_q, initial_K=K, fov_deg=fov_deg,
        reproj_error=32.0, min_inliers=6,
    )
    pnp_out = annotate_pnp_quality(pnp_out, min_score=4.0, min_inliers=6)

    if not pnp_out.get("success"):
        return {"success": False, "error": "ACE PnP 失败", "tag": tag, "elapsed": round(time.time() - t0, 2)}

    inliers_idx = pnp_out.get("inliers")
    if inliers_idx is not None and len(inliers_idx) > 0:
        idx = inliers_idx.ravel()
        idx = idx[idx < len(pts_3d)]
        best_obj, best_img = pts_3d[idx], pts_2d[idx]
    else:
        best_obj, best_img = pts_3d, pts_2d

    # 验证
    from services.localizer import _POINT_INDEX, load_colmap
    if _POINT_INDEX is None:
        load_colmap()
    las_tree = _POINT_INDEX.get("tree") if _POINT_INDEX else None
    las_result = {"total": 0, "verified": 0, "verification_rate": 0.0}
    if las_tree is not None and len(best_obj) > 0:
        las_result = verify_with_las_points(best_obj, las_tree, tol=5.0)

    R, _ = cv2.Rodrigues(pnp_out["rvec"])
    q_quat = rotation_matrix_to_quaternion(R)
    elapsed = round(time.time() - t0, 2)

    return {
        "success": True, "tag": tag,
        "reliable": las_result.get("verification_rate", 0) > 0.3,
        "pose": {
            "quaternion": q_quat.tolist(),
            "translation": pnp_out["tvec"].flatten().tolist(),
            "rotation_vector": pnp_out["rvec"].flatten().tolist(),
        },
        "quality": {
            "match_count": len(best_obj),
            "inlier_count": pnp_out.get("inlier_count", 0),
            "reprojection_error_px": round(pnp_out.get("reproj_error_px", 0), 2),
            "score": round(pnp_out.get("inlier_count", 0) * 0.1, 3),
        },
        "validations": {"las_nearest": las_result},
        "elapsed": elapsed,
        "spatial_config": {"method": "ace_6ch_normal_estimated"},
    }


# ────────────────────────────────────────────────────────────────────
# 2. ACE RGB-only（修改模型为 3ch）
# ────────────────────────────────────────────────────────────────────

def _adapt_model_to_rgb(model):
    """将 6ch 模型适配为 3ch（平均法线通道权重）"""
    with torch.no_grad():
        conv1 = model.encoder.conv1
        weight = conv1.weight.data  # [out, 6, 3, 3]
        # 只取前 3 通道（RGB），丢弃法线通道
        new_weight = weight[:, :3, :, :].clone()
        new_conv = torch.nn.Conv2d(3, conv1.out_channels, conv1.kernel_size,
                                    conv1.stride, conv1.padding)
        new_conv.weight.data = new_weight
        if conv1.bias is not None:
            new_conv.bias.data = conv1.bias.data.clone()
        model.encoder.conv1 = new_conv
    return model


def ace_rgb_only(image_path: str, output_dir: str = "projections/localize_ace",
                  fov_deg: float = 75.0, model_path: str = None, **kwargs) -> dict:
    """ACE 定位 — 只使用 RGB（修改模型为 3ch）"""
    tag = "ace_rgb"
    t0 = time.time()
    log(f"{'=' * 60}")
    log(f"🚀 ACE（RGB only）定位: {os.path.basename(image_path)}")

    from services.localizer.coord_regression import load_coord_regression, predict_dense

    # 支持指定模型路径（训练后自动使用新模型）
    if model_path and os.path.exists(model_path):
        model = load_coord_regression(model_path)
    else:
        model = load_coord_regression()
    model = _adapt_model_to_rgb(model)

    query_img = cv2.imread(image_path)
    if query_img is None:
        return {"success": False, "error": "Cannot read query image", "tag": tag}

    # ACE 预测（只传 RGB）
    with torch.no_grad():
        pts_2d, pts_3d, confidence = predict_dense(model, query_img)

    if len(pts_3d) < 6:
        return {"success": False, "error": "ACE 预测点不足", "tag": tag, "elapsed": round(time.time() - t0, 2)}

    if len(pts_3d) < 6:
        return {"success": False, "error": "ACE 预测点不足", "tag": tag, "elapsed": round(time.time() - t0, 2)}

    mask = confidence > 0.3
    pts_3d, pts_2d = pts_3d[mask], pts_2d[mask]
    if len(pts_3d) < 6:
        pts_3d, pts_2d = result['coords_3d'], result['points_2d']

    h_q, w_q = query_img.shape[:2]
    K = get_camera_matrix(w_q, h_q, fov_deg=fov_deg)

    # 降采样 + 宽松阈值（ACE 预测坐标不够精确）
    step = max(1, len(pts_3d) // 400)
    pnp_out = solve_pnp_with_focal_search(
        pts_3d[::step], pts_2d[::step], w_q, h_q, initial_K=K, fov_deg=fov_deg,
        reproj_error=32.0, min_inliers=6,
    )
    pnp_out = annotate_pnp_quality(pnp_out, min_score=4.0, min_inliers=6)

    if not pnp_out.get("success"):
        return {"success": False, "error": "ACE PnP 失败", "tag": tag, "elapsed": round(time.time() - t0, 2)}

    inliers_idx = pnp_out.get("inliers")
    if inliers_idx is not None and len(inliers_idx) > 0:
        idx = inliers_idx.ravel()
        idx = idx[idx < len(pts_3d)]
        best_obj, best_img = pts_3d[idx], pts_2d[idx]
    else:
        best_obj, best_img = pts_3d, pts_2d

    from services.localizer import _POINT_INDEX, load_colmap
    if _POINT_INDEX is None:
        load_colmap()
    las_tree = _POINT_INDEX.get("tree") if _POINT_INDEX else None
    las_result = {"total": 0, "verified": 0, "verification_rate": 0.0}
    if las_tree is not None and len(best_obj) > 0:
        las_result = verify_with_las_points(best_obj, las_tree, tol=5.0)

    R, _ = cv2.Rodrigues(pnp_out["rvec"])
    q_quat = rotation_matrix_to_quaternion(R)
    elapsed = round(time.time() - t0, 2)

    return {
        "success": True, "tag": tag,
        "reliable": las_result.get("verification_rate", 0) > 0.3,
        "pose": {
            "quaternion": q_quat.tolist(),
            "translation": pnp_out["tvec"].flatten().tolist(),
            "rotation_vector": pnp_out["rvec"].flatten().tolist(),
        },
        "quality": {
            "match_count": len(best_obj),
            "inlier_count": pnp_out.get("inlier_count", 0),
            "reprojection_error_px": round(pnp_out.get("reproj_error_px", 0), 2),
            "score": round(pnp_out.get("inlier_count", 0) * 0.1, 3),
        },
        "validations": {"las_nearest": las_result},
        "elapsed": elapsed,
        "spatial_config": {"method": "ace_3ch_rgb"},
    }


# ────────────────────────────────────────────────────────────────────
# 3. 深度估计 + ICP
# ────────────────────────────────────────────────────────────────────

def depth_icp(image_path: str, output_dir: str = "projections/localize_depth",
               fov_deg = 75.0, **kwargs) -> dict:
    """深度估计 + ICP 配准定位"""
    tag = "depth_icp"
    t0 = time.time()
    log(f"{'=' * 60}")
    log(f"🚀 深度估计+ICP 定位: {os.path.basename(image_path)}")

    query_img = cv2.imread(image_path)
    if query_img is None:
        return {"success": False, "error": "Cannot read query image", "tag": tag}

    h_q, w_q = query_img.shape[:2]

    # 1. 简单深度估计（使用梯度近似，快速但粗糙）
    #    实际应用中应使用 DepthAnything 等预训练模型
    gray = cv2.cvtColor(query_img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    # 简单假设：亮度越高越远（非常粗糙的近似）
    depth = gray / 255.0 * 50.0 + 1.0  # 1-50m 范围

    # 2. 提升为 3D 点云
    K = get_camera_matrix(w_q, h_q, fov_deg=fov_deg)
    fx, fy = K[0, 0], K[1, 1]
    cx, cy = K[0, 2], K[1, 2]

    u, v = np.meshgrid(np.arange(w_q), np.arange(h_q))
    z = depth
    x = (u - cx) * z / fx
    y = (v - cy) * z / fy

    # 采样点云
    step = 10  # 每 10 像素采样一个点
    points_3d = np.stack([x[::step, ::step].ravel(),
                          y[::step, ::step].ravel(),
                          z[::step, ::step].ravel()], axis=1)
    mask = np.isfinite(points_3d).all(axis=1) & (points_3d[:, 2] > 0)
    points_3d = points_3d[mask]

    if len(points_3d) < 100:
        return {"success": False, "error": "深度点云不足", "tag": tag, "elapsed": round(time.time() - t0, 2)}

    # 3. 加载 LAS 点云
    from services.localizer import get_point_cloud_arrays
    las_pts, _ = get_point_cloud_arrays()
    if len(las_pts) > 50000:
        idx = np.random.choice(len(las_pts), 50000, replace=False)
        las_pts = las_pts[idx]

    # 4. 粗略 ICP（使用 SIFT 匹配结果做初始对齐）
    #    简化：用当前位姿估计做投影验证
    #    完整 ICP 需要更复杂的实现，这里用 PnP + 深度验证

    # 使用深度图 + LAS 做粗略对齐
    # 取查询图中心区域的深度中值作为初始距离
    center_depth = np.median(depth[h_q//3:2*h_q//3, w_q//3:2*w_q//3])

    # 粗略位姿：假设相机在场景中心上方
    from services.localizer import _POINT_INDEX, load_colmap
    if _POINT_INDEX is None:
        load_colmap()

    # 简化：返回失败（完整 ICP 实现较复杂）
    return {
        "success": False,
        "error": "深度+ICP 需要预训练深度模型（如 DepthAnything）和完整 ICP 实现",
        "tag": tag,
        "elapsed": round(time.time() - t0, 2),
        "note": "当前使用粗糙深度估计，精度不足。建议：1) 加载 DepthAnything 模型 2) 实现完整 ICP",
    }
