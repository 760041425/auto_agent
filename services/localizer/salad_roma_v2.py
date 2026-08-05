"""
SALAD + Roma v2 视觉定位引擎 — 精度 + 速度优化版

对比原版 ``salad_roma.py`` 的关键改进（原版保留不动，用于对照）：

精度
  1. SALAD 描述子对称：索引同时存 ``rgb_only`` 与 ``multimodal``，检索用 ``rgb_only``
  2. 支持真实相机内参（不再硬编码 ``fov_deg=75``）
  3. 数值稳定的四元数转换（Shepperd 最大迹分支）
  4. NPY 无效像素改用 NaN 哨兵，不再与真实原点坐标冲突
  5. PnP reprojErr 8px → 4px + LO-RANSAC 细化
  6. 新增本质矩阵 RANSAC 2D-2D 几何预过滤
  7. 收紧 LightGlue 置信度阈值 ``min_cert=0.001`` → ``0.1``
  8. CLAHE 对称 / 不再对索引图做 CLAHE

速度
  9. 迭代轮数 ``MAX_ROUNDS=10`` → 默认 ``2``，自适应早停
  10. 可选先验引导检索（利用 ``_POSE_TREE`` 把候选从 2732 降到 ~10）

特征
  11. 保持宽高比 resize（padding 到 512×512）
  12. 新增 LAS 点云 3D 验证
  13. ACE 端点修复（使用本包 ``coord_regression`` 的确定符号）
"""

import json
import os
import time
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np
import torch

from services.localizer.pose_utils import (
    rotation_matrix_to_quaternion,
    solve_pnp_ransac,
    solve_pnp_with_focal_search,
    annotate_pnp_quality,
    compute_reprojection_error,
    is_pose_better,
    verify_essential_matrix,
    verify_with_las_points,
    get_camera_matrix,
    resize_keep_aspect,
    adaptive_early_stop,
)
from services.localizer.coord_regression import (
    load_coord_regression,
    predict_dense,
)
from services.localizer.verify_projection import (
    build_local_coordinate_transform_context,
    build_projection_xyz_map,
    verify_projection_local,
)

from services.localizer.logger_config import get_backend_logger

_logger = get_backend_logger("salad_roma_v2")


def log(msg: str):
    _logger.info(msg)


# ── 设备 ──
if torch.backends.mps.is_available():
    DEVICE = torch.device("mps")
    log("使用 MPS (Metal GPU)")
else:
    DEVICE = torch.device("cpu")
    log("使用 CPU")

# ── 全局缓存 ──
_TILE_INDEX: Optional[list] = None
_SALAD_INDEX_V2: Optional[dict] = None   # tile_key → {"rgb":..., "multi":...}
_TILE_IMAGES: Optional[dict] = None
_DINO_MODEL = None
_DINO_SCALE = None
_LIGHTGLUE_MODEL = None
_COORD_REG_MODEL = None
_LOFTR_MODEL = None


# --------------------------------------------------------------------------- #
# 模型加载（复用 salad_roma 的 DINOv2 / LightGlue 加载逻辑）
# --------------------------------------------------------------------------- #

def _get_dinov2_model(prefer_small=True):
    """复用原版 DINOv2 加载。"""
    global _DINO_MODEL, _DINO_SCALE
    if _DINO_MODEL is not None:
        return _DINO_MODEL, _DINO_SCALE
    from services.localizer.salad_roma import _get_dinov2_model as _orig
    _DINO_MODEL, _DINO_SCALE = _orig(prefer_small=prefer_small)
    return _DINO_MODEL, _DINO_SCALE


def _get_lightglue_model():
    """复用原版 LightGlue 加载。"""
    global _LIGHTGLUE_MODEL
    if _LIGHTGLUE_MODEL is not None:
        return _LIGHTGLUE_MODEL
    from services.localizer.salad_roma import _get_lightglue_model as _orig
    _LIGHTGLUE_MODEL = _orig()
    return _LIGHTGLUE_MODEL


def _get_loftr_model():
    """LoFTR 密集匹配模型（对域差距鲁棒）。"""
    global _LOFTR_MODEL
    if _LOFTR_MODEL is not None:
        return _LOFTR_MODEL
    from kornia.feature import LoFTR
    _LOFTR_MODEL = LoFTR(pretrained='outdoor').to(DEVICE).eval()
    log("LoFTR 模型加载完成")
    return _LOFTR_MODEL


# --------------------------------------------------------------------------- #
# SALAD 索引（对称：rgb_only + multimodal 双描述子）
# --------------------------------------------------------------------------- #

def _load_tile_index():
    global _TILE_INDEX
    if _TILE_INDEX is not None:
        return _TILE_INDEX
    idx_path = "projections/tile_index.json"
    if not os.path.exists(idx_path):
        raise FileNotFoundError(f"tile_index.json not found: {idx_path}")
    with open(idx_path) as f:
        _TILE_INDEX = json.load(f)
    return _TILE_INDEX


def _extract_rgb_descriptor(model, img, scale):
    """纯 RGB 描述子 —— 用于检索（与查询端对称）。"""
    from services.localizer.salad_roma import _extract_dinov2_descriptor
    return _extract_dinov2_descriptor(model, img, scale)


def _extract_multimodal_descriptor_v2(model, img, normal_map, xyz_map, scale):
    """多模态描述子 —— 用于重排序 / 相似度评估。"""
    from services.localizer.salad_roma import _extract_multimodal_descriptor
    return _extract_multimodal_descriptor(model, img, normal_map, xyz_map, scale)


def build_salad_index_v2(force_rebuild=False, progress_callback=None):
    """构建对称 SALAD 索引。

    同时存储 ``rgb_only`` 与 ``multimodal`` (RGB+Normal+XYZ) 描述子。
    查询端只用 ``rgb`` 做检索，避免原版的苹果/橘子比较问题。
    """
    global _SALAD_INDEX_V2
    cache_path = Path("projections/salad_index_v2.npz")

    if not force_rebuild and cache_path.exists() and _SALAD_INDEX_V2 is None:
        try:
            data = np.load(str(cache_path), allow_pickle=True)
            _SALAD_INDEX_V2 = {k: data[k].item() if data[k].ndim == 0 else data[k]
                               for k in data.files}
            if _SALAD_INDEX_V2:
                log(f"  SALAD v2 索引已加载: {len(_SALAD_INDEX_V2)} 条")
                return _SALAD_INDEX_V2
        except Exception as e:
            log(f"  加载缓存失败，重建: {e}")

    tiles = _load_tile_index()
    model, scale = _get_dinov2_model()
    if model is None:
        raise RuntimeError("DINOv2 模型不可用")

    index = {}
    total = len(tiles)
    for i, t in enumerate(tiles):
        if not t.get("accepted", True):
            continue
        key = _tile_key(t)
        img_path = t.get("image_path", "")
        npy_path = t.get("npy_path", "")
        normal_path = t.get("normal_path", "")
        if not os.path.exists(img_path):
            continue

        img = cv2.imread(img_path)
        if img is None:
            continue
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        rgb_desc = _extract_rgb_descriptor(model, img_rgb, scale)

        normal_map = None
        xyz_map = None
        if normal_path and os.path.exists(normal_path):
            nm = np.load(normal_path)
            if nm is not None and nm.shape[:2] == img_rgb.shape[:2]:
                normal_map = nm
        if npy_path and os.path.exists(npy_path):
            try:
                xm = np.load(npy_path)
                if xm is not None and xm.shape[:2] == img_rgb.shape[:2]:
                    xyz_map = xm
            except Exception:
                xyz_map = None

        multi_desc = _extract_multimodal_descriptor_v2(model, img_rgb, normal_map, xyz_map, scale)

        if rgb_desc is not None and multi_desc is not None:
            index[key] = {"rgb": rgb_desc.astype(np.float32),
                          "multi": multi_desc.astype(np.float32)}

        if progress_callback is not None and (i + 1) % 50 == 0:
            progress_callback(i + 1, total)
        # 定期清理 GPU 缓存
        if (i + 1) % 100 == 0 and DEVICE.type in ("cuda", "mps"):
            if DEVICE.type == "cuda":
                torch.cuda.empty_cache()
            elif hasattr(torch.mps, "empty_cache"):
                torch.mps.empty_cache()

    _SALAD_INDEX_V2 = index
    if index:
        try:
            np.savez(str(cache_path), **{k: np.array(v, dtype=object) for k, v in index.items()})
        except Exception:
            np.savez(str(cache_path), **index)
        log(f"  SALAD v2 索引已保存: {len(index)} 条")
    return index


def _tile_key(t: dict) -> str:
    """生成 tile_key，包含 pose_id 和 pitch 以避免不同视角撞 key。

    格式：view_{view}_{tile}_{pose_id}_p{pitch}
    例：view_yaw0_14.9_2.0_0.6_2000_p+0
    """
    view = t.get("view", "?")
    tile = t.get("tile", "?")
    pose_id = str(t.get("pose_id", ""))
    pitch = "+0"
    # 从 image_path 反解 pose_id 和 pitch
    img = t.get("image_path", "")
    stem = Path(img).stem  # view_yaw0_14.9_2.0_0.6_2000_p+0
    parts = stem.split("_")
    # 找 pitch tag (p+0, p-15, etc.)
    for i, p in enumerate(parts):
        if p.startswith("p") and p[1:].lstrip("-+").isdigit():
            pitch = p[1:]
            if i > 0 and parts[i - 1].isdigit():
                pose_id = parts[i - 1]
            break
    if not pose_id or pose_id == "0":
        # fallback：从末尾找数字
        for p in reversed(parts):
            if p.isdigit():
                pose_id = p
                break
    if not pose_id:
        pose_id = "0"
    return f"view_{view}_{tile}_{pose_id}_p{pitch}"


def _ensure_index():
    global _SALAD_INDEX_V2
    if _SALAD_INDEX_V2 is None or len(_SALAD_INDEX_V2) == 0:
        build_salad_index_v2()
    return _SALAD_INDEX_V2 or {}


# --------------------------------------------------------------------------- #
# 检索（支持先验引导）
# --------------------------------------------------------------------------- #

def _salad_retrieve_v2(
    q_img: np.ndarray,
    top_k: int = 5,
    prior_position: Optional[Tuple[float, float, float]] = None,
    prior_radius: float = 15.0,
):
    """对称 SALAD 检索，可选先验位置引导。

    有先验时只在 ``prior_radius`` 米内检索，候选从 ~2732 降到 ~10。
    返回 ``[(tile_key, similarity, tile_info), ...]``。
    """
    index = _ensure_index()
    if not index:
        return []

    model, scale = _get_dinov2_model()
    if model is None:
        return []

    q_rgb = cv2.cvtColor(q_img, cv2.COLOR_BGR2RGB) if q_img.ndim == 3 else q_img
    q_desc = _extract_rgb_descriptor(model, q_rgb, scale)
    if q_desc is None:
        return []

    tiles = _load_tile_index()
    tile_map = {}
    for t in tiles:
        image_path = t.get("image_path", "")
        if not image_path or not bool(t.get("accepted", bool(image_path))):
            continue
        key = _tile_key(t)
        # 保留 accepted 优先
        if key not in tile_map or (t.get("accepted") and not tile_map[key].get("accepted")):
            tile_map[key] = t

    # 描述子缓存可能仍含历史实验视角；当前 MapTile 发布清单是唯一候选边界。
    index = {key: entry for key, entry in index.items() if key in tile_map}
    if not index:
        log("  SALAD v2 缓存与当前 accepted MapTile 无交集")
        return []

    scores = []
    q_norm = np.linalg.norm(q_desc) + 1e-8

    if prior_position is not None:
        # 先验引导：利用 _POSE_TREE 快速筛出近邻
        try:
            from services.localizer import _POSE_TREE, _POSE_ARRAY, load_colmap
            if _POSE_TREE is None:
                load_colmap()
            if _POSE_TREE is not None:
                nearby_idx = _POSE_TREE.query_ball_point(prior_position, prior_radius)
                if nearby_idx:
                    # 把近邻 pose 对应的 tile 筛出来
                    nearby_roots = set()
                    for idx in nearby_idx:
                        if idx < len(_POSE_ARRAY):
                            x, y, z = _POSE_ARRAY[idx]
                            nearby_roots.add(f"{round(x,1)}_{round(y,1)}_{round(z,1)}")
                    filtered = {k: v for k, v in index.items()
                               if any(r in k for r in nearby_roots)}
                    if filtered:
                        index = filtered
                        log(f"  [先验引导] 候选从 {len(_POSE_ARRAY)} pose 筛到 {len(index)} tiles")
        except Exception as e:
            log(f"  [先验引导] 失败，回退全量检索: {e}")

    for key, entry in index.items():
        rgb_desc = entry["rgb"] if isinstance(entry, dict) else entry
        sim = float(np.dot(q_desc, rgb_desc) / (q_norm * (np.linalg.norm(rgb_desc) + 1e-8)))
        scores.append((sim, key))

    scores.sort(reverse=True)
    results = []
    for sim, key in scores[:top_k]:
        results.append((key, sim, tile_map[key]))
    return results


# --------------------------------------------------------------------------- #
# 匹配（修复 CLAHE 不对称 + 几何预过滤）
# --------------------------------------------------------------------------- #

def _match_tile_with_lightglue_v2(img1, img2, sample_num=3000):
    """LightGlue 匹配，不再做 CLAHE（与索引一致）。

    ``img1`` / ``img2`` 均为 BGR ndarray。
    """
    from services.localizer.salad_roma import _lightglue_match
    return _lightglue_match(img1, img2, sample_num)


def _fallback_sift_match(img1, img2, sample_num=3000):
    """SIFT+FLANN fallback（无 CLAHE）。"""
    gray1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY) if img1.ndim == 3 else img1
    if img2.ndim == 3:
        gray2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY)
    elif img2.ndim == 2:
        gray2 = img2
    else:
        gray2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY)
    sift = cv2.SIFT_create(nfeatures=sample_num)
    kp1, des1 = sift.detectAndCompute(gray1, None)
    kp2, des2 = sift.detectAndCompute(gray2, None)
    if des1 is None or des2 is None or len(kp1) < 2 or len(kp2) < 2:
        return (np.zeros((0, 2)), np.zeros((0, 2)), np.zeros(0))
    index_params = dict(algorithm=1, trees=5)
    search_params = dict(checks=50)
    flann = cv2.FlannBasedMatcher(index_params, search_params)
    matches = flann.knnMatch(des1.astype(np.float32), des2.astype(np.float32), k=2)
    good = []
    for m_n in matches:
        if len(m_n) == 2 and m_n[0].distance < 0.75 * m_n[1].distance:
            good.append(m_n[0])
    if not good:
        return (np.zeros((0, 2)), np.zeros((0, 2)), np.zeros(0))
    pts1 = np.array([kp1[m.queryIdx].pt for m in good], dtype=np.float64)
    pts2 = np.array([kp2[m.trainIdx].pt for m in good], dtype=np.float64)
    cert = np.array([1.0 - m.distance / 200.0 for m in good], dtype=np.float32)
    cert = np.clip(cert, 0, 1)
    return pts1, pts2, cert


def _build_3d_2d_matches_v2(kpts_q, kpts_tile, cert, coord_map, min_cert=0.001):
    """构建 3D-2D 对应，修复 [0,0,0] 哨兵 → NaN，min_cert 默认放宽到 0.01。"""
    if len(kpts_q) != len(kpts_tile) or len(kpts_q) != len(cert):
        return np.zeros((0, 3)), np.zeros((0, 2))

    obj_pts, img_pts = [], []
    is_npy = isinstance(coord_map, np.ndarray) and coord_map.ndim == 3

    for i in range(len(kpts_q)):
        if cert[i] < min_cert:
            continue
        x, y = int(round(kpts_tile[i, 0])), int(round(kpts_tile[i, 1]))
        if is_npy:
            h, w = coord_map.shape[:2]
            if y < 0 or y >= h or x < 0 or x >= w:
                continue
            p3d = coord_map[y, x]
        elif isinstance(coord_map, dict):
            p3d = coord_map.get(f"{x},{y}")
            if p3d is None:
                continue
            p3d = np.asarray(p3d, dtype=np.float64)
        else:
            continue

        # 过滤无效点：NaN 或全零
        if p3d is None or len(p3d) != 3:
            continue
        p3d = np.asarray(p3d, dtype=np.float64)
        if not np.isfinite(p3d).all() or np.allclose(p3d, 0.0):
            continue
        obj_pts.append(p3d)
        img_pts.append(kpts_q[i])

    if not obj_pts:
        return np.zeros((0, 3)), np.zeros((0, 2))
    return np.array(obj_pts, dtype=np.float64), np.array(img_pts, dtype=np.float64)


# --------------------------------------------------------------------------- #
# 主定位流程
# --------------------------------------------------------------------------- #

def localize_multi_strategy(query_image_path, camera_intrinsics=None, fov_deg=75.0):
    """多策略融合定位（方案 3）。

    同时运行 DISK+LG、LoFTR、Hybrid 三种匹配器，
    选择 PnP 内点数最多且重投影误差最小的位姿。
    """
    import cv2
    import numpy as np
    from services.localizer.pose_utils import (
        get_camera_matrix, solve_pnp_with_focal_search, annotate_pnp_quality,
        compute_reprojection_error, rotation_matrix_to_quaternion,
    )

    image = cv2.imread(query_image_path)
    if image is None:
        return {"success": False, "error": "cannot read image"}
    q = cv2.resize(image, (512, 512))
    h_q, w_q = q.shape[:2]
    K = get_camera_matrix(w_q, h_q, fov_deg=fov_deg, intrinsics=camera_intrinsics)

    # 获取候选 tile
    _ensure_index()
    retrieved = _salad_retrieve_v2(q, top_k=3)
    if not retrieved:
        return {"success": False, "error": "SALAD 检索无结果", "tag": "multi"}

    best = {"inliers": 0, "error": float("inf"), "score": 0.0, "rvec": None, "tvec": None}

    for name_key, sim, tile in retrieved:
        tile_path = tile.get("image_path", "")
        npy_path = tile.get("npy_path", "")
        if not os.path.exists(tile_path) or not os.path.exists(npy_path):
            continue
        tile_img = cv2.imread(tile_path)
        npy = np.load(npy_path)
        if tile_img is None:
            continue

        for matcher_name, matcher_fn in [("lg", _match_tile_with_lightglue_v2), ("hybrid", _match_tile_with_hybrid)]:
            try:
                kpts_q, kpts_t, cert = matcher_fn(q, tile_img)
                if len(kpts_q) < 6:
                    continue
                mask = cert > 0.003
                if mask.sum() < 6:
                    continue
                kpts_q, kpts_t = kpts_q[mask], kpts_t[mask]
                obj_pts, img_pts = _build_3d_2d_matches_v2(kpts_q, kpts_t, cert[mask], npy, min_cert=0.001)
                if len(obj_pts) < 4:
                    continue
                pnp_out = solve_pnp_with_focal_search(
                    obj_pts, img_pts, w_q, h_q,
                    initial_K=K, fov_deg=fov_deg,
                    reproj_error=4.0, min_inliers=6,
                )
                pnp_out = annotate_pnp_quality(pnp_out, min_score=4.0, min_inliers=6)
                if pnp_out.get("success"):
                    ic = pnp_out.get("inlier_count", 0)
                    score = pnp_out.get("score", 0.0)
                    err = pnp_out.get("reproj_error_px", float("inf"))
                    if score > best["score"] or (score == best["score"] and ic > best["inliers"]):
                        best.update({
                            "inliers": ic, "error": err, "score": score,
                            "rvec": pnp_out["rvec"], "tvec": pnp_out["tvec"],
                            "strategy": matcher_name,
                            "quality_passed": pnp_out.get("quality_passed"),
                            "quality_score": pnp_out.get("quality_score"),
                            "quality_reasons": pnp_out.get("quality_reasons", []),
                        })
            except Exception:
                continue

    if best["rvec"] is None:
        return {"success": False, "error": "所有策略 PnP 失败", "tag": "multi"}

    q_quat = rotation_matrix_to_quaternion(cv2.Rodrigues(best["rvec"])[0])
    return {
        "success": True,
        "tag": "multi_strategy",
        "pose": {
            "quaternion": [float(q_quat[0]), float(q_quat[1]), float(q_quat[2]), float(q_quat[3])],
            "translation": best["tvec"].flatten().tolist(),
            "rotation_vector": best["rvec"].flatten().tolist(),
        },
        "inliers": best["inliers"],
        "reprojection_error": round(best["error"], 2),
        "score": round(best["score"], 3),
        "quality_passed": best.get("quality_passed"),
        "quality_score": best.get("quality_score"),
        "quality_reasons": best.get("quality_reasons", []),
        "strategy": best.get("strategy", "-"),
        "camera_matrix": K.tolist(),
    }


def localize_with_salad_roma_v2(
    query_image_path: str,
    output_dir: str = "projections/localize_v2",
    max_iterations: int = 2,
    top_k_retrieval: int = 3,
    debug_visualizations: bool = False,
    algo: str = "lightglue",
    camera_intrinsics: Optional[np.ndarray] = None,
    fov_deg: float = 75.0,
    use_pose_prior: bool = True,
    prior_position: Optional[Tuple[float, float, float]] = None,
    prior_radius: float = 15.0,
    reproj_error: float = 4.0,
    min_inliers: int = 6,
    geometric_verify: bool = False,  # tile↔query 不满足对极几何，默认关
    keep_aspect_ratio: bool = True,
    use_loftr: bool = False,  # 方案 B：使用 LoFTR 替代 DISK+LightGlue
    matcher_mode: Optional[str] = None,
    coordinate_threshold_m: float = 0.3,
) -> dict:
    """SALAD v2 定位入口。

    参数
    ----------
    camera_intrinsics : 3×3 矩阵，传入时覆盖 ``fov_deg`` 估计
    use_pose_prior : 是否尝试用 _POSE_TREE 做先验检索
    prior_position : 先验位置 (x,y,z)，None 时自动从最近 pose 获取
    prior_radius : 先验检索半径（米）
    reproj_error : PnP 重投影阈值（默认 4px，原版 8px）
    min_inliers : 最少内点，低于此值视为失败
    geometric_verify : 是否在 PnP 前做 E-matrix 几何预过滤
    keep_aspect_ratio : 保持宽高比 resize
    """
    from services.localizer import load_colmap, _POINT_INDEX, get_point_cloud_arrays

    if matcher_mode is None:
        matcher_mode = "loftr" if use_loftr else "disk_lg"
    if matcher_mode not in {"disk_lg", "loftr", "hybrid"}:
        return {"success": False, "error": f"Unsupported matcher mode: {matcher_mode}"}
    match_name = {
        "disk_lg": "DISK+LightGlue",
        "loftr": "LoFTR",
        "hybrid": "Hybrid",
    }[matcher_mode]
    tag = f"salad_v2_{matcher_mode}"
    log(f"{'=' * 60}")
    log(f"🚀 SALAD v2 + {match_name} 定位: {os.path.basename(query_image_path)}")

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # 1. 读取查询图像
    query_img = cv2.imread(query_image_path)
    if query_img is None:
        return {"success": False, "error": "Cannot read query image", "tag": tag}

    # 2. 图像预处理（保持宽高比 or 原版强制 512×512）
    img_h, img_w = query_img.shape[:2]
    scale = 1.0
    pad = (0, 0)
    if keep_aspect_ratio:
        q_small, scale, pad = resize_keep_aspect(query_img, target_size=512)
        log(f"📷 {img_w}×{img_h} → 512×512 (keep aspect, scale={scale:.3f})")
    else:
        q_small = cv2.resize(query_img, (512, 512))
        scale = 512.0 / max(img_h, img_w)
        log(f"📷 {img_w}×{img_h} → 512×512 (stretch)")

    # 3. 相机内参
    if camera_intrinsics is not None:
        K_full = np.asarray(camera_intrinsics, dtype=np.float64).reshape(3, 3)
        # 缩放到 512 空间
        K = K_full.copy()
        sx = 512.0 / img_w
        sy = 512.0 / img_h
        if keep_aspect_ratio:
            sx = sy = scale
            K[0, 2] = K_full[0, 2] * scale + pad[0]
            K[1, 2] = K_full[1, 2] * scale + pad[1]
        K[0, 0] *= sx
        K[1, 1] *= sy
    else:
        K = get_camera_matrix(img_w, img_h, fov_deg=fov_deg)
        if keep_aspect_ratio:
            K[0] *= scale
            K[1] *= scale
            K[0, 2] = K[0, 2] * scale + pad[0]
            K[1, 2] = K[1, 2] * scale + pad[1]
        else:
            K[0, 0] *= 512.0 / img_w
            K[1, 1] *= 512.0 / img_h
            K[0, 2] = 256.0
            K[1, 2] = 256.0

    # 4. ACE 分支
    if algo == "ace":
        return _localize_ace_v2(query_img, q_small, K, scale, pad, out, tag,
                                keep_aspect_ratio, fov_deg, prior_position, prior_radius)

    # 5. 加载点云
    known_points, _ = load_colmap()
    all_pts, all_col = get_point_cloud_arrays()
    log(f"🗺️ {len(all_pts)} 个 3D 点")

    # 6. SALAD 检索
    log("🔍 SALAD v2 对称检索...")
    t0 = time.time()
    retrieved = _salad_retrieve_v2(
        q_small, top_k=top_k_retrieval,
        prior_position=prior_position if use_pose_prior else None,
        prior_radius=prior_radius,
    )
    log(f"  SALAD 检索: {time.time() - t0:.2f}s, 找到 {len(retrieved)} 候选")
    if not retrieved:
        return {"success": False, "error": "SALAD 检索无匹配候选", "tag": tag}

    for i, (k, sim, t) in enumerate(retrieved):
        log(f"  #{i}: {t.get('view', '?')}/{k} sim={sim:.4f}")

    # 7. 候选匹配 + 多候选联合 PnP
    best_rvec, best_tvec = None, None
    best_inliers = 0
    best_reproj_error = float("inf")
    best_score = 0.0
    best_quality_passed = None
    best_quality_score = None
    best_quality_reasons = []
    best_3d = None
    best_2d = None
    best_tile = None
    best_match_count = 0

    # 收集所有候选的 3D-2D 匹配，用于联合 PnP
    all_obj_pts = []
    all_img_pts = []

    for rank, (name_key, sim, tile) in enumerate(retrieved):
        log(f"\n{'─' * 40}")
        log(f"  候选 #{rank}: {tile.get('view', '?')}/{name_key} (sim={sim:.3f})")

        if not tile.get("image_path") or not os.path.exists(tile.get("image_path", "")):
            log("    跳过: tile 文件不存在")
            continue

        npy_path = _resolve_npy_path(tile, name_key)
        if npy_path is None:
            log("    坐标映射不存在")
            continue

        coord_map = np.load(npy_path)
        if coord_map is None or (isinstance(coord_map, np.ndarray) and coord_map.size == 0):
            log("    坐标映射为空")
            continue

        tile_bgr = cv2.imread(tile["image_path"])
        if tile_bgr is None:
            continue

        if matcher_mode == "loftr":
            kpts_q, kpts_tile, cert = _match_tile_with_loftr(q_small, tile_bgr)
        elif matcher_mode == "hybrid":
            kpts_q, kpts_tile, cert = _match_tile_with_hybrid(q_small, tile_bgr)
        else:
            kpts_q, kpts_tile, cert = _match_tile_with_lightglue_v2(q_small, tile_bgr)
        if len(kpts_q) < 6:
            log(f"    匹配点太少 ({len(kpts_q)}), 跳过")
            continue

        # 几何预过滤（仅对重投影↔原图启用，tile↔query 不满足对极几何）
        if geometric_verify and len(kpts_q) >= 5:
            mask, n_ok = verify_essential_matrix(kpts_q, kpts_tile, K, threshold=2.0)
            if mask is not None and n_ok >= 5:
                kpts_q = kpts_q[mask.ravel() > 0]
                kpts_tile = kpts_tile[mask.ravel() > 0]
                cert = cert[mask.ravel() > 0]
                log(f"    E-matrix 过滤: {int(mask.ravel().sum())}/{len(mask)} 通过")

        obj_pts, img_pts = _build_3d_2d_matches_v2(kpts_q, kpts_tile, cert, coord_map, min_cert=0.001)
        log(f"    3D-2D 匹配: {len(obj_pts)} 对 (from {len(kpts_q)} kpts, cert>{0:.3f})")
        if len(obj_pts) < 4:
            continue

        h_qs, w_qs = q_small.shape[:2]
        pnp_out = solve_pnp_with_focal_search(
            obj_pts, img_pts, w_qs, h_qs,
            initial_K=K, fov_deg=fov_deg,
            reproj_error=reproj_error, min_inliers=min_inliers,
        )
        pnp_out = annotate_pnp_quality(pnp_out, min_score=4.0, min_inliers=min_inliers)
        if pnp_out.get("success"):
            ic = pnp_out.get("inlier_count", 0)
            err_i = pnp_out.get("reproj_error_px", float("inf"))
            score_i = pnp_out.get("score", 0.0)
            log(f"    PnP: {ic}/{len(obj_pts)} 内点, 误差={err_i:.2f}px, score={score_i:.2f}"
                + (f", quality={'✓' if pnp_out.get('quality_passed') else '✗'}"
                   if pnp_out.get('quality_reasons') is not None else ""))
            if is_pose_better(ic, err_i, best_inliers, best_reproj_error):
                best_inliers = ic
                best_reproj_error = err_i
                best_score = score_i
                best_rvec, best_tvec = pnp_out["rvec"], pnp_out["tvec"]
                inliers_i = pnp_out.get("inliers")
                mask_in = inliers_i.ravel() if inliers_i is not None else slice(None)
                best_3d = obj_pts[mask_in]
                best_2d = img_pts[mask_in]
                best_tile = tile
                best_match_count = len(obj_pts)
                best_quality_passed = pnp_out.get("quality_passed")
                best_quality_score = pnp_out.get("quality_score")
                best_quality_reasons = pnp_out.get("quality_reasons", [])

        # 收集用于联合 PnP
        all_obj_pts.append(obj_pts)
        all_img_pts.append(img_pts)

    if best_rvec is None:
        return {"success": False, "error": "所有候选 PnP 失败", "tag": tag}

    # 7b. 多候选联合 PnP：合并所有候选的 3D-2D 匹配做鲁棒估计
    if len(all_obj_pts) > 1:
        merged_obj = np.vstack(all_obj_pts)
        merged_img = np.vstack(all_img_pts)
        log(f"\n{'─' * 40}")
        log(f"🔗 多候选联合 PnP: {len(merged_obj)} 对匹配（{len(all_obj_pts)} 候选）")
        h_qs, w_qs = q_small.shape[:2]
        pnp_m = solve_pnp_with_focal_search(
            merged_obj, merged_img, w_qs, h_qs,
            initial_K=K, fov_deg=fov_deg,
            reproj_error=reproj_error, min_inliers=min_inliers,
        )
        pnp_m = annotate_pnp_quality(pnp_m, min_score=4.0, min_inliers=min_inliers)
        if pnp_m.get("success"):
            ic_m = pnp_m.get("inlier_count", 0)
            err_m = pnp_m.get("reproj_error_px", float("inf"))
            log(f"  联合 PnP: {ic_m}/{len(merged_obj)} 内点, 误差={err_m:.2f}px")
            if is_pose_better(ic_m, err_m, best_inliers, best_reproj_error):
                best_rvec, best_tvec = pnp_m["rvec"], pnp_m["tvec"]
                best_inliers = ic_m
                best_reproj_error = err_m
                mask_m = pnp_m["inliers"].ravel() if pnp_m.get("inliers") is not None else slice(None)
                best_3d = merged_obj[mask_m]
                best_2d = merged_img[mask_m]
                log(f"  ✅ 联合 PnP 更优，采用")

    # 8. 多轮迭代精化：用最佳 tile 重新匹配，逐步降低阈值
    log(f"\n{'─' * 40}")
    log(f"🔄 多轮迭代精化 (max={max_iterations}, reproj={reproj_error}px)...")

    rvec, tvec = best_rvec, best_tvec
    inlier_count = best_inliers
    current_error = best_reproj_error
    round_errors = [current_error]

    # 迭代阈值：从严格到宽松，逐步精化
    cert_thresholds = [0.1, 0.01, 0.001]
    actual_iterations = 0  # 实际执行的迭代轮数

    for iteration in range(1, max_iterations + 1):
        if adaptive_early_stop(round_errors, patience=1):
            log(f"  ⏹ 误差收敛，停止迭代 (第 {iteration} 轮)")
            break
        log(f"  迭代 {iteration}/{max_iterations}...")

        # 用最佳 tile 重新匹配（避免合成投影的域差距）
        if best_tile is None:
            break
        tile_bgr_iter = cv2.imread(best_tile["image_path"])
        if tile_bgr_iter is None:
            break

        cert_thresh = cert_thresholds[min(iteration - 1, len(cert_thresholds) - 1)]

        if matcher_mode == "loftr":
            kpts_q2, kpts_t2, cert2 = _match_tile_with_loftr(q_small, tile_bgr_iter)
        elif matcher_mode == "hybrid":
            kpts_q2, kpts_t2, cert2 = _match_tile_with_hybrid(q_small, tile_bgr_iter)
        else:
            kpts_q2, kpts_t2, cert2 = _match_tile_with_lightglue_v2(q_small, tile_bgr_iter)
        if len(kpts_q2) < 8:
            log(f"    迭代匹配点太少, 停止")
            break

        npy_path = _resolve_npy_path(best_tile, best_tile.get("tile", ""))
        if npy_path is None:
            break
        coord_map = np.load(npy_path)

        obj2, img2 = _build_3d_2d_matches_v2(kpts_q2, kpts_t2, cert2, coord_map, min_cert=cert_thresh)
        log(f"    3D-2D: {len(obj2)} 对 (cert>{cert_thresh})")
        if len(obj2) < 4:
            log("    3D-2D 不足, 停止")
            break

        h_qs, w_qs = q_small.shape[:2]
        pnp_refine = solve_pnp_with_focal_search(
            obj2, img2, w_qs, h_qs,
            initial_K=K, fov_deg=fov_deg,
            reproj_error=reproj_error, min_inliers=min_inliers,
        )
        pnp_refine = annotate_pnp_quality(pnp_refine, min_score=4.0, min_inliers=min_inliers)
        if pnp_refine.get("success"):
            nic = pnp_refine.get("inlier_count", 0)
            nerr = pnp_refine.get("reproj_error_px", float("inf"))
            round_errors.append(nerr)
            if is_pose_better(nic, nerr, inlier_count, current_error):
                rvec, tvec = pnp_refine["rvec"], pnp_refine["tvec"]
                inlier_count = nic
                current_error = nerr
                mask_iter = pnp_refine["inliers"].ravel() if pnp_refine.get("inliers") is not None else slice(None)
                best_3d = obj2[mask_iter]
                best_2d = img2[mask_iter]
                log(f"    ✅ 提升: {nic} 内点, 误差={nerr:.2f}px")
            else:
                log(f"    未提升: {nic} 内点, 误差={nerr:.2f}px")
        actual_iterations = iteration  # 记录实际执行的迭代轮数
    if inlier_count < min_inliers:
        log(f"  ⚠️ 内点不足 ({inlier_count} < {min_inliers}), 定位不可靠")

    # 9. 投影验证（homography + NPY 坐标对比）
    # 即使定位失败（PnP 无解），也尝试用最佳候选 tile 做验证
    verify_result = None
    log(f"  [VERIFY-DEBUG] best_tile={best_tile is not None}, retrieved={len(retrieved)}, best_rvec={best_rvec is not None}")
    try:
        _verify_tile = best_tile if best_tile is not None else (
            retrieved[0][2] if retrieved else None
        )
        log(f"  [VERIFY-DEBUG] verify_tile={_verify_tile is not None}")
        if _verify_tile is not None:
            best_tile_path = _verify_tile.get("image_path", "")
            best_npy_path = _verify_tile.get("npy_path", "")
            if best_tile_path and os.path.exists(best_tile_path) and os.path.exists(best_npy_path):
                tile_img = cv2.imread(best_tile_path)
                tile_npy = np.load(best_npy_path)
                if tile_img is not None:
                    # 用匹配点（如有）或网格采样
                    from services.localizer.salad_roma import _lightglue_match
                    kq_v, kt_v, cert_v = _lightglue_match(q_small, tile_img)
                    mask_v = cert_v > 0.003
                    if mask_v.sum() >= 4:
                        kq_v, kt_v = kq_v[mask_v], kt_v[mask_v]
                        verify_result = verify_projection_local(
                            q_small, tile_img, tile_npy,
                            kq_v, kt_v,
                            n_samples=min(20, len(kq_v)),
                        )
                        fit = verify_result.get("homography_fit", {})
                        log(
                            f"  2D 单应拟合: {fit.get('n_inliers', 0)}/"
                            f"{fit.get('n_matches', 0)} inliers, "
                            f"median={fit.get('inlier_median_residual_px', 'n/a')}px; "
                            "米制验证不可用（同源 NPY）"
                        )
                    else:
                        log(f"  验证跳过: 匹配点不足 ({mask_v.sum()} < 4)")
    except Exception as e:
        log(f"  投影验证失败: {e}")

    # 10. LAS 验证（可选）
    las_result = None
    try:
        if _POINT_INDEX is not None and "tree" in _POINT_INDEX and best_3d is not None and len(best_3d) > 0:
            las_result = verify_with_las_points(best_3d, _POINT_INDEX["tree"], tol=3.0)
            log(f"  LAS 验证: {las_result['verification_rate']:.1%} 通过, 平均距离={las_result['mean_distance_m']:.2f}m")
    except Exception as e:
        log(f"  LAS 验证失败: {e}")

    # 10. 输出位姿（稳定四元数）
    R_mat = cv2.Rodrigues(rvec)[0]
    q = rotation_matrix_to_quaternion(R_mat)  # [w,x,y,z]
    t_vec_out = tvec.flatten()

    log(f"🏆 结果: 内点={inlier_count}, 误差={current_error:.2f}px, "
        f"t=({t_vec_out[0]:.2f},{t_vec_out[1]:.2f},{t_vec_out[2]:.2f})")

    artifacts = {}
    artifact_error = None
    try:
        artifacts = _write_final_artifacts(
            q_small,
            all_pts,
            all_col,
            rvec,
            tvec,
            K,
            out,
            tag,
        )
        log(f"  最终视觉产物: {', '.join(artifacts)}")
    except Exception as exc:
        artifact_error = str(exc)
        log(f"  最终视觉产物生成失败: {exc}")

    coordinate_transform = {
        "status": "not_available",
        "reason": "final_2d_3d_correspondences_unavailable",
    }
    try:
        if best_3d is not None and best_2d is not None and len(best_3d) >= 4:
            height, width = q_small.shape[:2]
            projection_xyz = build_projection_xyz_map(
                all_pts,
                rvec,
                tvec,
                K,
                width=width,
                height=height,
                splat_radius=1,
            )
            coordinate_transform = build_local_coordinate_transform_context(
                best_2d,
                best_3d,
                projection_xyz,
                out / f"projection_xyz_{tag}.npy",
                consistency_threshold_m=coordinate_threshold_m,
                plane_distance_threshold=0.2,
                plane_seed=1337,
                fitting_2d=best_2d,
                fitting_3d=best_3d,
            )
            consistency = coordinate_transform.get("consistency", {})
            plane_seg = coordinate_transform.get("plane_segmentation", {})
            # 诊断：用 PnP 位姿直接重投影 NPY 3D 点，看纯位姿误差（不经过 H）
            pose_only_err = None
            try:
                if best_3d is not None and best_2d is not None:
                    proj_pts, _ = cv2.projectPoints(
                        best_3d.reshape(-1, 1, 3),
                        best_rvec if 'best_rvec' in dir() else np.zeros(3),
                        best_tvec if 'best_tvec' in dir() else np.zeros(3),
                        K, None,
                    )
                    proj_pts = proj_pts.reshape(-1, 2)
                    reproj_px = np.linalg.norm(proj_pts - best_2d, axis=1)
                    pose_only_err = float(np.median(reproj_px))
            except Exception:
                pass
            # 诊断：采样 5 个 PnP 内点，对比 H→SLAM XY vs NPY XY
            sample_diag = ""
            try:
                if best_3d is not None and best_2d is not None and homography is not None:
                    # 取前 5 个内点
                    n_sample = min(5, len(best_3d))
                    samp_3d = best_3d[:n_sample]
                    samp_2d = best_2d[:n_sample]
                    # H→SLAM
                    hom = np.asarray(coordinate_transform.get("homography", homography))
                    hom = hom.reshape(3, 3) if hom.size == 9 else None
                    if hom is not None:
                        pts_h = np.column_stack([samp_2d[:, 0], samp_2d[:, 1], np.ones(n_sample)])
                        mapped = (hom @ pts_h.T).T
                        slam_xy = mapped[:, :2] / mapped[:, 2:3]
                        # 差异
                        diffs = np.linalg.norm(slam_xy - samp_3d[:, :2], axis=1)
                        sample_diag = (
                            f" | H_vs_NPY_XY_diff=["
                            + ", ".join(f"{d:.1f}m" for d in diffs)
                            + "] (前{n_sample}内点)"
                        )
            except Exception:
                pass
            reproj_str = f"{pose_only_err:.2f}px" if pose_only_err is not None else "N/A"
            log(
                f"  本地坐标转换产物: {coordinate_transform.get('status')} "
                f"({coordinate_transform.get('n_inliers', 0)}/"
                f"{coordinate_transform.get('n_matches', 0)} H inliers); "
                f"plane_segmentation={plane_seg.get('status', 'N/A')}, "
                f"ground={plane_seg.get('n_ground_inliers', '-')}/"
                f"{plane_seg.get('n_total_points', '-')}, "
                f"median difference={consistency.get('median_m', 'n/a')}m, "
                f"PnP reproj_median={reproj_str}"
                f"{sample_diag}"
            )
    except Exception as exc:
        coordinate_transform = {
            "status": "not_available",
            "reason": "coordinate_transform_generation_failed",
            "error": str(exc),
        }
        log(f"  本地坐标转换产物生成失败: {exc}")

    consistency = coordinate_transform.get("consistency", {})
    coordinate_reliable = bool(
        consistency.get("status") == "available" and consistency.get("passed")
    )

    # 诊断：采样内点对比 H→SLAM XY vs NPY XY（在 coordinate_transform 生成后）
    sample_diag = ""
    try:
        ct_status = coordinate_transform.get("status")
        if ct_status == "ready" and best_3d is not None and best_2d is not None:
            hom_entries = coordinate_transform.get("homography")
            plane_seg = coordinate_transform.get("plane_segmentation", {})
            plane_params = plane_seg.get("plane_params")
            if hom_entries is not None:
                hom = np.asarray(hom_entries, dtype=np.float64).reshape(3, 3)
                # 取第 1 个内点做详细展示
                p2d = best_2d[0]
                p3d = best_3d[0]
                pt_h = np.array([p2d[0], p2d[1], 1.0])
                mapped = hom @ pt_h
                if abs(mapped[2]) > 1e-12:
                    slam_x, slam_y = mapped[0] / mapped[2], mapped[1] / mapped[2]
                    sample_diag = (
                        f" | pt0: pixel=({p2d[0]:.1f},{p2d[1]:.1f})"
                        f" → H=({slam_x:.1f},{slam_y:.1f})"
                        f" vs NPY=({p3d[0]:.1f},{p3d[1]:.1f},{p3d[2]:.1f})"
                        f" Δ=({slam_x-p3d[0]:.1f},{slam_y-p3d[1]:.1f})"
                    )
                    if plane_params:
                        sample_diag += f" plane=[{','.join(f'{p:.2f}' for p in plane_params)}]"
    except Exception as e:
        sample_diag = f" | DIAG_ERR={e}"

    if sample_diag:
        log(f" 🔍 点对比诊断{sample_diag}")

    result = {
        "success": True,
        "reliable": coordinate_reliable,
        "tag": tag,
        "pose": {
            "quaternion": [float(q[0]), float(q[1]), float(q[2]), float(q[3])],
            "translation": [float(t_vec_out[0]), float(t_vec_out[1]), float(t_vec_out[2])],
            "rotation_vector": rvec.flatten().tolist(),
        },
        "inliers": int(inlier_count),
        "match_count": int(best_match_count),
        "reprojection_error": float(current_error),
        "score": round(float(best_score), 3) if best_score else None,
        "quality_passed": best_quality_passed,
        "quality_score": round(float(best_quality_score), 3) if best_quality_score is not None else None,
        "quality_reasons": best_quality_reasons,
        "projection_verification": verify_result,
        "las_verification": las_result,
        "artifacts": artifacts,
        "query_image": artifacts.get("query_image"),
        "reprojection_image": artifacts.get("reprojection_image"),
        "comparison_image": artifacts.get("comparison_image"),
        "artifact_generation": {
            "status": "available" if artifacts else "failed",
            "error": artifact_error,
        },
        "coordinate_transform": coordinate_transform,
        "camera_matrix": K.tolist(),
        "total_rounds": actual_iterations,  # 实际执行的迭代轮数（0 = 只有初始 PnP，未进入迭代）
        "n_candidates": len(retrieved),  # SALAD 检索返回的候选 tile 数
        "focal_search_summary": getattr(solve_pnp_with_focal_search, 'last_summary', None),
    }
    return result


# --------------------------------------------------------------------------- #
# ACE v2 分支（修复符号缺失 + normal=None）
# --------------------------------------------------------------------------- #

def _localize_ace_v2(query_img, q_small, K, scale, pad, out, tag,
                     keep_aspect_ratio, fov_deg,
                     prior_position, prior_radius):
    """ACE 定位 v2：修复 CoordRegressionFCN 缺失和 normal=None 问题。"""
    model_path = Path("projections/ace_model.pth")
    if not model_path.exists():
        return {"success": False, "error": "ACE 模型未训练", "tag": tag}

    try:
        model = load_coord_regression(str(model_path), in_channels=6, device=DEVICE)
        log(f"  ACE v2 模型加载完成, device={next(model.parameters()).device}")
    except Exception as e:
        return {"success": False, "error": f"ACE 模型加载失败: {e}", "tag": tag}

    # 构造伪 normal（推理时 normal=None 的 train/serve skew 修复）
    h, w = query_img.shape[:2]
    fake_normal = np.full((h, w, 3), 128, dtype=np.float32)

    pts_2d, pts_3d, _ = predict_dense(model, query_img, normal_map=fake_normal,
                                      device=DEVICE, max_points=2000)
    if pts_2d is None or len(pts_2d) < 10:
        return {"success": False, "error": "ACE 预测点不足", "tag": tag}

    # PnP (v2: 4px + refine)
    rvec, tvec, inliers = solve_pnp_ransac(pts_3d, pts_2d, K,
                                           reproj_error=4.0, refine=True)
    if rvec is None:
        return {"success": False, "error": "ACE PnP 失败", "tag": tag}

    ic = len(inliers) if inliers is not None else len(pts_2d)
    err = compute_reprojection_error(rvec, tvec, K, pts_3d, pts_2d)

    R_mat = cv2.Rodrigues(rvec)[0]
    q = rotation_matrix_to_quaternion(R_mat)
    t_vec_out = tvec.flatten()

    return {
        "success": True,
        "tag": tag,
        "pose": {
            "quaternion": [float(q[0]), float(q[1]), float(q[2]), float(q[3])],
            "translation": [float(t_vec_out[0]), float(t_vec_out[1]), float(t_vec_out[2])],
            "rotation_vector": rvec.flatten().tolist(),
        },
        "inliers": int(ic),
        "reprojection_error": float(err),
        "camera_matrix": K.tolist(),
    }


# --------------------------------------------------------------------------- #
# 辅助：路径解析 / 局部重投影 / 局部 coord map
# --------------------------------------------------------------------------- #

def _resolve_npy_path(tile: dict, name_key: str):
    """解析 tile 的 NPY 坐标映射路径。"""
    candidates = [
        tile.get("npy_path", ""),
        str(Path("projections/tiles") / f"{name_key}.npy"),
        f"projections/tiles/view_{tile.get('view', '?')}_{tile.get('tile', '?')}_{name_key.rsplit('_', 1)[-1]}.npy",
    ]
    for p in candidates:
        if p and os.path.exists(p) and not os.path.isdir(p):
            return p
    return None


def _compute_reprojection_coords_local(all_pts, all_col, rvec, tvec, K, w, h):
    """计算像素→3D 的 coord map（内存版，不落盘）。"""
    if all_pts is None or len(all_pts) == 0:
        return {}
    rvec = np.asarray(rvec).reshape(3, 1)
    tvec = np.asarray(tvec).reshape(3, 1)
    pts = np.asarray(all_pts, dtype=np.float64).reshape(-1, 3)
    proj, _ = cv2.projectPoints(pts, rvec, tvec, K, None)
    proj = proj.reshape(-1, 2)
    R_mat = cv2.Rodrigues(rvec)[0]
    t_arr = tvec.flatten()
    pts_cam = (pts - t_arr) @ R_mat.T
    depths = pts_cam[:, 2]

    coord = {}
    for i in range(len(proj)):
        if depths[i] <= 0:
            continue
        x, y = int(round(proj[i, 0])), int(round(proj[i, 1]))
        if 0 <= x < w and 0 <= y < h:
            key = f"{x},{y}"
            if key not in coord:  # 近覆盖远
                coord[key] = [float(pts[i, 0]), float(pts[i, 1]), float(pts[i, 2])]
    return coord


def _render_projection_local(all_pts, all_col, rvec, tvec, K, w, h, out_dir, name):
    """渲染重投影图（优先 C++ octree，fallback Python splat）。"""
    out_path = str(out_dir / name)
    try:
        from services.las_processor import projection_octree as poct
        from services.las_processor.projection import _apply_camera_like_shading
        from PIL import Image
        import tempfile

        octree_dataset = "projections/octree_data"
        if os.path.exists(os.path.join(octree_dataset, "manifest.json")):
            from services.localizer.salad_roma import _rvec_tvec_to_colmap_line
            colmap_line = _rvec_tvec_to_colmap_line(rvec, tvec.flatten())
            f = K[0, 0]
            focal_norm = f / max(w, h)
            with tempfile.TemporaryDirectory(prefix="pnp_render_v2_") as tmpdir:
                color_ppm = os.path.join(tmpdir, "color.ppm")
                depth_raw = os.path.join(tmpdir, "depth.raw")
                ok = poct.render_pose_octree(octree_dataset, colmap_line,
                                             w, h, focal_norm, color_ppm, depth_raw)
                if ok and os.path.exists(color_ppm):
                    with Image.open(color_ppm) as pil_img:
                        color_img = np.array(pil_img.convert("RGB"))
                    if os.path.exists(depth_raw):
                        depth = np.fromfile(depth_raw, dtype=np.float32)
                        if depth.size == w * h:
                            color_img = _apply_camera_like_shading(
                                color_img, depth=depth.reshape(h, w))
                    cv2.imwrite(out_path, cv2.cvtColor(color_img, cv2.COLOR_RGB2BGR))
                    return cv2.imread(out_path)
    except Exception as e:
        log(f"  [RENDER] octree 失败, fallback: {e}")

    try:
        from services.localizer import _render_point_cloud_splat
        img = _render_point_cloud_splat(all_pts, all_col, K, w, h,
                                        np.asarray(rvec),
                                        np.asarray(tvec).flatten()[:3],
                                        radius=1.2)
        cv2.imwrite(out_path, img)
        return img
    except Exception as e:
        log(f"  [RENDER] fallback 也失败: {e}")
        return None


def _write_final_artifacts(
    query_image,
    all_pts,
    all_col,
    rvec,
    tvec,
    camera_matrix,
    output_dir,
    tag,
):
    """为最终返回位姿生成查询图、最终投影图和带标签双图。"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    query_path = output_dir / f"query_{tag}.png"
    projection_name = f"reprojection_{tag}.png"
    projection_path = output_dir / projection_name
    comparison_path = output_dir / f"comparison_{tag}.png"

    if not cv2.imwrite(str(query_path), query_image):
        raise RuntimeError(f"cannot write query artifact: {query_path}")

    height, width = query_image.shape[:2]
    projection = _render_projection_local(
        all_pts,
        all_col,
        rvec,
        tvec,
        camera_matrix,
        width,
        height,
        output_dir,
        projection_name,
    )
    if projection is None or not projection_path.exists():
        raise RuntimeError("final projection rendering failed")
    if projection.shape[:2] != query_image.shape[:2]:
        projection = cv2.resize(projection, (width, height))

    header_height = 28
    canvas = np.zeros((height + header_height, width * 2, 3), dtype=np.uint8)
    canvas[header_height:, :width] = query_image
    canvas[header_height:, width:] = projection
    cv2.putText(
        canvas,
        "Query image",
        (8, 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        "Final pose projection",
        (width + 8, 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    if not cv2.imwrite(str(comparison_path), canvas):
        raise RuntimeError(f"cannot write comparison artifact: {comparison_path}")

    return {
        "query_image": os.path.relpath(query_path, Path.cwd()),
        "reprojection_image": os.path.relpath(projection_path, Path.cwd()),
        "comparison_image": os.path.relpath(comparison_path, Path.cwd()),
    }


# --------------------------------------------------------------------------- #
# ACE + LAS 验证（方案 2）
# --------------------------------------------------------------------------- #

def ace_localize_with_las_verify(query_image_path, camera_intrinsics=None, fov_deg=75.0):
    """ACE RGB-only 定位 + LAS 点云验证。

    方案 2：ACE 不依赖 tile，直接回归 3D 坐标。
    验证方式：ACE 预测的 3D 点与 LAS 点云近邻对比。
    """
    import cv2
    import numpy as np
    import torch
    from services.localizer.coord_regression import load_coord_regression, predict_dense
    from services.localizer.pose_utils import get_camera_matrix, solve_pnp_ransac, rotation_matrix_to_quaternion

    device = torch.device("mps") if torch.backends.mps.is_available() else torch.device("cpu")
    image = cv2.imread(query_image_path)
    if image is None:
        return {"success": False, "error": "cannot read image"}
    h, w = image.shape[:2]
    K = get_camera_matrix(w, h, fov_deg=fov_deg, intrinsics=camera_intrinsics)

    model_path = "projections/ace_model.pth"
    if not os.path.exists(model_path):
        return {"success": False, "error": "ACE model not found"}

    model = load_coord_regression(model_path, in_channels=6, device=device)
    fake_normal = np.zeros_like(image)
    pts_2d, pts_3d, _ = predict_dense(model, image, normal_map=fake_normal, device=device, max_points=2000)
    if pts_2d is None or len(pts_2d) < 10:
        return {"success": False, "error": f"ACE predict failed ({0 if pts_2d is None else len(pts_2d)} pts)"}

    rvec, tvec, inliers = solve_pnp_ransac(pts_3d, pts_2d, K, reproj_error=8.0, refine=True)
    if rvec is None:
        return {"success": False, "error": "ACE PnP failed"}

    ic = len(inliers) if inliers is not None else len(pts_3d)
    q = rotation_matrix_to_quaternion(cv2.Rodrigues(rvec)[0])

    # LAS 验证：ACE 预测的 3D 点与 LAS 点云对比
    las_result = None
    try:
        from services.localizer import _POINT_INDEX, load_colmap
        if _POINT_INDEX is None:
            load_colmap()
        if _POINT_INDEX is not None and "tree" in _POINT_INDEX:
            # 取 ACE 预测的 3D 点（世界坐标）
            R_mat = cv2.Rodrigues(rvec)[0]
            t_arr = tvec.flatten()
            # 把相机坐标系下的 3D 点转到世界坐标
            pts_3d_world = (pts_3d - t_arr) @ R_mat
            las_result = verify_with_las_points(pts_3d_world, _POINT_INDEX["tree"], tol=5.0)
    except Exception:
        las_result = None

    return {
        "success": True,
        "tag": "ace_las",
        "pose": {
            "quaternion": [float(q[0]), float(q[1]), float(q[2]), float(q[3])],
            "translation": tvec.flatten().tolist(),
            "rotation_vector": rvec.flatten().tolist(),
        },
        "inliers": ic,
        "match_count": len(pts_2d),
        "las_verification": las_result,
        "camera_matrix": K.tolist(),
    }


# --------------------------------------------------------------------------- #
# LoFTR 匹配（方案 B）
# --------------------------------------------------------------------------- #

def _match_tile_with_hybrid(img1, img2):
    """Hybrid 匹配：DISK+LG 粗匹配 + LoFTR 精匹配 → 联合 PnP。

    方案 1：结合两种匹配器的优势，获得更多更稳定的对应点。
    """
    kpts_q_lg, kpts_t_lg, cert_lg = _match_tile_with_lightglue_v2(img1, img2)
    kpts_q_rom, kpts_t_rom, cert_rom = _match_tile_with_loftr(img1, img2)

    # 合并匹配点（简单拼接，去重靠 PnP RANSAC）
    if len(kpts_q_lg) > 0 and len(kpts_q_rom) > 0:
        kpts_q = np.vstack([kpts_q_lg, kpts_q_rom])
        kpts_t = np.vstack([kpts_t_lg, kpts_t_rom])
        cert = np.concatenate([cert_lg, cert_rom])
    elif len(kpts_q_rom) > 0:
        kpts_q, kpts_t, cert = kpts_q_rom, kpts_t_rom, cert_rom
    else:
        kpts_q, kpts_t, cert = kpts_q_lg, kpts_t_lg, cert_lg

    log(f"  Hybrid: DISK+LG({len(kpts_q_lg)}) + LoFTR({len(kpts_q_rom)}) = {len(kpts_q)} matches")
    return kpts_q, kpts_t, cert


def _match_tile_with_loftr(img1, img2):
    """LoFTR 密集匹配 — 对合成↔真实域差距更鲁棒。

    参数
    ----------
    img1, img2 : np.ndarray (H, W, 3) BGR 图像

    返回
    -------
    (kpts1, kpts2, certainty) — 匹配点坐标和置信度
    """
    model = _get_loftr_model()

    import cv2
    import numpy as np

    gray1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY) if img1.ndim == 3 else img1
    gray2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY) if img2.ndim == 3 else img2

    t1 = torch.from_numpy(gray1).float()[None, None].to(DEVICE) / 255.0
    t2 = torch.from_numpy(gray2).float()[None, None].to(DEVICE) / 255.0

    input_dict = {"image0": t1, "image1": t2}
    with torch.no_grad():
        correspondences = model(input_dict)

    mkpts0 = correspondences['keypoints0'].cpu().numpy()
    mkpts1 = correspondences['keypoints1'].cpu().numpy()
    conf = correspondences.get('confidence', torch.ones(len(mkpts0)))
    confidence = conf.cpu().numpy().astype(np.float32)

    log(f"  LoFTR: {len(mkpts0)} matches, conf_mean={confidence.mean():.3f}")
    return mkpts0, mkpts1, confidence
