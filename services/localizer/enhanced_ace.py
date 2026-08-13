"""
增强版 ACE 定位 — 三个改进方案

1. train_ace_on_scene  — 在当前场景重新训练 ACE
2. ace_with_better_normal — 使用更好的法线估计
3. depth_anything_icp  — DepthAnything + ICP
"""

import os
import time
from typing import Dict, Optional, Tuple

import cv2
import numpy as np
import torch

from services.localizer.logger_config import get_backend_logger

_logger = get_backend_logger("enhanced_ace")


def log(msg: str):
    _logger.info(msg)


# ────────────────────────────────────────────────────────────────────
# 007 法线 skew 修复：常量占位 / 3ch 场景模型路由
# ────────────────────────────────────────────────────────────────────

SCENE_MODEL_PATH = "projections/ace_model_scene.pth"
DEFAULT_MODEL_PATH = "projections/ace_model.pth"
CONSTANT_NORMAL_VALUE = 0.5  # ≈ 训练真法线 (n+1)*0.5 映射后的分布均值中心

INPUT_MODE_RGB3CH = "ace_scene_rgb3ch"
INPUT_MODE_6CH_CONSTANT = "ace_6ch_constant_normal"
NORMAL_SOURCE_CONSTANT = "constant_fallback"
NORMAL_SOURCE_NONE = "none_rgb3ch"


def resolve_ace_model(
    scene_model_path: Optional[str] = None,
    default_model_path: Optional[str] = None,
) -> Tuple[object, Dict[str, object]]:
    """路由 ACE 模型：场景 3ch RGB-only 优先，否则回退默认 6ch。

    返回 ``(model, info)``，``info`` 含：
    - ``model_path`` / ``in_channels``
    - ``input_mode``：``ace_scene_rgb3ch``（3ch 自洽推理，不传法线）或
      ``ace_6ch_constant_normal``（6ch 回退，传常量 0.5 法线占位）
    - ``normal_source``：``none_rgb3ch`` / ``constant_fallback``

    3ch 场景模型不存在或通道检测异常时自动降级 6ch（行为降级而非崩溃）。
    """
    from services.localizer.coord_regression import _detect_architecture, load_coord_regression

    scene_path = scene_model_path or SCENE_MODEL_PATH
    default_path = default_model_path or DEFAULT_MODEL_PATH

    if os.path.exists(scene_path):
        try:
            state_dict = torch.load(scene_path, map_location="cpu", weights_only=False)
            cfg = _detect_architecture(state_dict)
            if cfg.get("in_channels", 6) == 3:
                model = load_coord_regression(scene_path)
                info = {
                    "model_path": scene_path,
                    "in_channels": 3,
                    "input_mode": INPUT_MODE_RGB3CH,
                    "normal_source": NORMAL_SOURCE_NONE,
                }
                return model, info
            log(f"scene 模型非 3ch（in_channels={cfg.get('in_channels')}），回退 6ch")
        except Exception as exc:  # noqa: BLE001 路由降级不允许崩溃
            log(f"scene 3ch 模型加载失败，回退默认 6ch: {exc}")

    model = load_coord_regression(default_path)
    info = {
        "model_path": default_path,
        "in_channels": 6,
        "input_mode": INPUT_MODE_6CH_CONSTANT,
        "normal_source": NORMAL_SOURCE_CONSTANT,
    }
    return model, info


def build_constant_normal_map(h: int, w: int) -> np.ndarray:
    """构造常量 0.5 法线占位（无信息，与训练真法线分布均值对齐）。"""
    return np.full((h, w, 3), CONSTANT_NORMAL_VALUE, dtype=np.float32)


def _las_bbox_and_overlap(
    pred: np.ndarray,
    las_pts,
) -> Tuple[Optional[dict], float]:
    """预测 3D 点是否落在 LAS 点云 bbox 内 → 重叠率（0..1）。"""
    if las_pts is None or len(las_pts) == 0 or pred is None or len(pred) == 0:
        return None, 0.0
    pts = np.asarray(las_pts, dtype=np.float64)
    mn = pts.min(axis=0)
    mx = pts.max(axis=0)
    bbox = {
        "x_min": float(mn[0]), "x_max": float(mx[0]),
        "y_min": float(mn[1]), "y_max": float(mx[1]),
        "z_min": float(mn[2]), "z_max": float(mx[2]),
    }
    inside = (
        (pred[:, 0] >= mn[0]) & (pred[:, 0] <= mx[0])
        & (pred[:, 1] >= mn[1]) & (pred[:, 1] <= mx[1])
        & (pred[:, 2] >= mn[2]) & (pred[:, 2] <= mx[2])
    )
    overlap = float(inside.sum() / len(pred))
    return bbox, overlap


def build_ace_failure_diagnostics(
    pts_3d: np.ndarray,
    model_info: dict,
    pnp_out: Optional[dict] = None,
    las_pts=None,
) -> dict:
    """组装 ACE 系失败诊断：PNP 统计 + 预测 3D 分布 vs LAS bbox 重叠率。"""
    pred = np.asarray(pts_3d, dtype=np.float64)
    if pred.ndim != 2 or pred.shape[1] != 3 or len(pred) == 0:
        pred_xyz = {"z_min": None, "z_max": None, "center": None, "count": 0}
    else:
        z = pred[:, 2]
        pred_xyz = {
            "z_min": float(z.min()),
            "z_max": float(z.max()),
            "center": pred.mean(axis=0).tolist(),
            "count": int(len(pred)),
        }

    asc = (pnp_out or {}).get("attempts_summary") or {}
    pnp_stats = {
        "tried_candidates": asc.get("tried_candidates"),
        "best_inliers": asc.get("best_inliers"),
        "best_reproj_error_px": asc.get("best_reproj_error_px"),
    }

    las_bbox, overlap = _las_bbox_and_overlap(pred, las_pts)
    return {
        "pnp": pnp_stats,
        "pred_xyz": pred_xyz,
        "las_bbox": las_bbox,
        "overlap_with_las_bbox": overlap,
        "model": {
            "path": model_info.get("model_path"),
            "in_channels": model_info.get("in_channels"),
        },
        "input_mode": model_info.get("input_mode"),
    }


# ────────────────────────────────────────────────────────────────────
# 方案 1: 在当前场景重新训练 ACE
# ────────────────────────────────────────────────────────────────────

def train_ace_on_scene(epochs=50, model_save_path="projections/ace_model_scene.pth"):
    """在当前场景上训练 RGB-only ACE 模型"""
    from services.localizer.ace_trainer import train_ace_rgb

    log(f"🚀 开始训练 ACE（{epochs} epochs）...")
    t0 = time.time()

    train_ace_rgb(
        tile_index_path="projections/tile_index.json",
        model_save_path=model_save_path,
        epochs=epochs,
        batch_size=2,
        lr=1e-3,
    )

    elapsed = time.time() - t0
    log(f"✅ ACE 训练完成: {elapsed:.1f}s")
    return model_save_path


# ────────────────────────────────────────────────────────────────────
# 方案 2: 更好的法线估计
# ────────────────────────────────────────────────────────────────────

def _estimate_gradient_normal(rgb_image):
    """梯度近似法线（Sobel 伪法线）—— debug-only，非真实 DSINE。

    与训练期真实 LAS 法线分布不符（高频噪声），默认推理不再使用；
    仅在显式传入 ``debug_normal=True`` 时用于对比诊断。
    """
    # 基于梯度的法线估计 + 平滑
    gray = cv2.cvtColor(rgb_image, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0

    # 多尺度梯度
    normals = []
    for ksize in [3, 5, 7]:
        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=ksize)
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=ksize)
        normal = np.stack([-gx, -gy, np.ones_like(gray)], axis=-1)
        norm = np.linalg.norm(normal, axis=-1, keepdims=True)
        normal = normal / (norm + 1e-8)
        normals.append(normal)

    # 平均多尺度
    normal = np.mean(normals, axis=0)
    norm = np.linalg.norm(normal, axis=-1, keepdims=True)
    normal = normal / (norm + 1e-8)

    # 高斯平滑
    normal = cv2.GaussianBlur(normal, (5, 5), 1.0)

    # 映射到 [0, 1]
    normal = (normal + 1.0) * 0.5
    return normal.astype(np.float32)


def ace_with_better_normal(image_path: str, output_dir: str = "projections/localize_ace",
                            fov_deg: float = 75.0, debug_normal: bool = False,
                            normal_mode: str = "constant",
                            _scene_model_path: Optional[str] = None,
                            **kwargs) -> dict:
    """ACE 定位 — 法线与训练对齐（3ch 场景模型优先 / 6ch 按 normal_mode 选择法线）

    ``normal_mode``（D-008-02，默认 "constant" 不变）：
    - ``"constant"``：常量 0.5 占位（007 行为不变）
    - ``"dsine"``：调用 ``estimate_normal`` 喂真实法线；模型不可用自动回退常量
      并标注 ``normal_source="constant_fallback"``

    ``_scene_model_path``（内部/基准用，默认 None）：传入非 None 时覆盖
    ``resolve_ace_model`` 的场景模型路径——传不存在的路径可强制走 6ch 回退路由，
    用于 008 P3 基准的四路径对比。日常调用不传。
    """
    tag = "ace_better_normal"
    t0 = time.time()
    log(f"{'=' * 60}")
    log(f"🚀 ACE（更好法线）定位: {os.path.basename(image_path)}")

    from services.localizer.coord_regression import predict_dense
    from services.localizer.pose_utils import get_camera_matrix, solve_pnp_with_focal_search, annotate_pnp_quality, rotation_matrix_to_quaternion, verify_with_las_points
    from services.localizer import _POINT_INDEX, load_colmap

    model, model_info = resolve_ace_model(scene_model_path=_scene_model_path)
    input_mode = model_info["input_mode"]
    normal_source = model_info["normal_source"]

    query_img = cv2.imread(image_path)
    if query_img is None:
        return {"success": False, "error": "Cannot read query image", "tag": tag}

    # 法线构造：3ch 路径不产生法线；6ch 路径按 normal_mode 选择来源
    # （dsine 真法线 > debug 梯度对比 > 常量 0.5 占位，008 只改动 dsine 分支）
    h, w = query_img.shape[:2]
    normal_map = None
    if input_mode == INPUT_MODE_6CH_CONSTANT:
        if normal_mode == "dsine":
            from services.localizer.normal_estimator import estimate_normal, normal_source_from_estimate
            normal_map = estimate_normal(query_img)
            normal_source = normal_source_from_estimate()
        elif debug_normal:
            normal_map = _estimate_gradient_normal(query_img)
            normal_map = cv2.resize(normal_map, (w, h), interpolation=cv2.INTER_LINEAR)
            normal_source = "gradient_debug"
        else:
            normal_map = build_constant_normal_map(h, w)

    # ACE 预测
    with torch.no_grad():
        pts_2d, pts_3d, confidence = predict_dense(model, query_img, normal_map=normal_map)

    if len(pts_3d) < 6:
        las_pts = _POINT_INDEX.get("pts") if _POINT_INDEX is not None else None
        diagnostics = build_ace_failure_diagnostics(pts_3d, model_info, pnp_out=None, las_pts=las_pts)
        return {
            "success": False, "error": "ACE 预测点不足", "tag": tag,
            "elapsed": round(time.time() - t0, 2),
            "input_mode": input_mode, "normal_source": normal_source,
            "diagnostics": diagnostics,
        }

    # PnP
    h_q, w_q = query_img.shape[:2]
    K = get_camera_matrix(w_q, h_q, fov_deg=fov_deg)

    step = max(1, len(pts_3d) // 400)
    pnp_out = solve_pnp_with_focal_search(
        pts_3d[::step], pts_2d[::step], w_q, h_q, initial_K=K, fov_deg=fov_deg,
        reproj_error=32.0, min_inliers=6,
    )
    pnp_out = annotate_pnp_quality(pnp_out, min_score=4.0, min_inliers=6)

    if not pnp_out.get("success"):
        las_pts = _POINT_INDEX.get("pts") if _POINT_INDEX is not None else None
        diagnostics = build_ace_failure_diagnostics(pts_3d, model_info, pnp_out=pnp_out, las_pts=las_pts)
        return {
            "success": False, "error": "ACE PnP 失败", "tag": tag,
            "elapsed": round(time.time() - t0, 2),
            "input_mode": input_mode, "normal_source": normal_source,
            "diagnostics": diagnostics,
        }

    inliers_idx = pnp_out.get("inliers")
    if inliers_idx is not None and len(inliers_idx) > 0:
        idx = inliers_idx.ravel()
        idx = idx[idx < len(pts_3d)]
        best_obj = pts_3d[idx] if len(idx) > 0 else pts_3d
        best_img = pts_2d[idx] if len(idx) > 0 else pts_2d
    else:
        best_obj, best_img = pts_3d, pts_2d

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
        "spatial_config": {"method": "ace_better_normal"},
        "input_mode": input_mode,
        "normal_source": normal_source,
    }


# ────────────────────────────────────────────────────────────────────
# 方案 3: DepthAnything + ICP
# ────────────────────────────────────────────────────────────────────

def depth_anything_icp(image_path: str, output_dir: str = "projections/localize_depth",
                        fov_deg: float = 75.0, **kwargs) -> dict:
    """深度估计 + ICP 配准定位"""
    tag = "depth_anything"
    t0 = time.time()
    log(f"{'=' * 60}")
    log(f"🚀 深度估计+ICP 定位: {os.path.basename(image_path)}")

    query_img = cv2.imread(image_path)
    if query_img is None:
        return {"success": False, "error": "Cannot read query image", "tag": tag}

    h_q, w_q = query_img.shape[:2]

    # 1. 尝试加载 DepthAnything 或类似模型
    depth_model = _load_depth_model()

    if depth_model is None:
        return {
            "success": False,
            "error": "未找到深度估计模型。请安装: pip install depth-anything",
            "tag": tag,
            "elapsed": round(time.time() - t0, 2),
            "note": "安装后需要下载预训练权重",
        }

    # 2. 估计深度
    with torch.no_grad():
        depth = depth_model(query_img)

    # 3. 提升为 3D 点云
    K = get_camera_matrix(w_q, h_q, fov_deg=fov_deg)
    fx, fy = K[0, 0], K[1, 1]
    cx, cy = K[0, 2], K[1, 2]

    u, v = np.meshgrid(np.arange(w_q), np.arange(h_q))
    z = depth
    x = (u - cx) * z / fx
    y = (v - cy) * z / fy

    # 采样
    step = 5
    points_3d = np.stack([x[::step, ::step].ravel(),
                          y[::step, ::step].ravel(),
                          z[::step, ::step].ravel()], axis=1)
    mask = np.isfinite(points_3d).all(axis=1) & (points_3d[:, 2] > 0.5) & (points_3d[:, 2] < 100)
    points_3d = points_3d[mask]

    if len(points_3d) < 100:
        return {"success": False, "error": "深度点云不足", "tag": tag, "elapsed": round(time.time() - t0, 2)}

    # 4. 加载 LAS 点云
    from services.localizer import get_point_cloud_arrays
    las_pts, _ = get_point_cloud_arrays()
    if len(las_pts) > 30000:
        idx = np.random.choice(len(las_pts), 30000, replace=False)
        las_pts = las_pts[idx]

    # 5. 粗略 ICP（使用 PCA 初始对齐 + 迭代最近点）
    # 简化：使用质心对齐 + 旋转估计
    centroid_query = points_3d.mean(axis=0)
    centroid_las = las_pts.mean(axis=0)

    # 初始平移
    initial_translation = centroid_las - centroid_query

    # 验证：用初始平移投影点云
    points_3d_aligned = points_3d + initial_translation

    # 计算到 LAS 的最近邻距离
    from scipy.spatial import cKDTree
    tree = cKDTree(las_pts)
    dists, _ = tree.query(points_3d_aligned)
    mean_dist = dists.mean()

    elapsed = round(time.time() - t0, 2)

    # 返回粗略结果（完整 ICP 需要更多迭代）
    return {
        "success": True, "tag": tag,
        "reliable": mean_dist < 5.0,  # 粗略阈值
        "pose": {
            "quaternion": [1, 0, 0, 0],  # 无旋转（简化）
            "translation": initial_translation.tolist(),
            "rotation_vector": [0, 0, 0],
        },
        "quality": {
            "match_count": len(points_3d),
            "inlier_count": int((dists < 5.0).sum()),
            "reprojection_error_px": None,
            "score": round(max(0, 10 - mean_dist), 3),
        },
        "validations": {
            "mean_distance_to_las": round(mean_dist, 3),
            "note": "粗略对齐，完整 ICP 需要更多迭代",
        },
        "elapsed": elapsed,
        "spatial_config": {"method": "depth_icp_approximate"},
    }


def _load_depth_model():
    """尝试加载深度估计模型"""
    try:
        # 尝试 DepthAnything
        from depth_anything.dpt import DepthAnything
        model = DepthAnything.from_pretrained("LiheYoung/depth_anything_vitb14")
        model.eval()
        return model
    except ImportError:
        pass

    try:
        # 尝试 MiDaS
        model = torch.hub.load("intel-isl/MiDaS", "MiDaS_small")
        model.eval()
        return model
    except Exception:
        pass

    return None
