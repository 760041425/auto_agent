import json
import logging
import os
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

# 日志配置
LOG_DIR = Path(__file__).parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
_logger = logging.getLogger("matcher")
_logger.setLevel(logging.DEBUG)
_fh = logging.FileHandler(LOG_DIR / "matcher.log", mode="a", encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
_logger.handlers.clear()
_logger.addHandler(_fh)

# 同时输出到 stdout
_sh = logging.StreamHandler()
_sh.setFormatter(logging.Formatter("%(asctime)s [MATCHER] %(message)s", datefmt="%H:%M:%S"))
_logger.addHandler(_sh)


def log_match_step(msg: str, task_id: int | None = None):
    prefix = f"[Task#{task_id}]" if task_id else ""
    _logger.info(f"{prefix} {msg}")


def _extract_features(image_path: str, method: str = "sift", task_id: int | None = None):
    """提取图像特征，支持多种算法

    算法: sift, orb, akaze, brisk, kaze
    """
    img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        log_match_step(f"❌ 无法读取图像: {image_path}", task_id)
        return None, None, None
    
    n_features = 5000
    if method == "sift":
        detector = cv2.SIFT_create(nfeatures=n_features)
    elif method == "orb":
        detector = cv2.ORB_create(nfeatures=n_features)
    elif method == "akaze":
        detector = cv2.AKAZE_create()
    elif method == "brisk":
        detector = cv2.BRISK_create()
    elif method == "kaze":
        detector = cv2.KAZE_create()
    else:
        detector = cv2.SIFT_create(nfeatures=n_features)
    
    kp, des = detector.detectAndCompute(img, None)
    n_kp = len(kp) if kp is not None else 0
    n_des = des.shape if des is not None else (0,)
    log_match_step(f"📷 {method.upper()}提取: {os.path.basename(image_path)} → {n_kp}个特征点, des={n_des}", task_id)
    return kp, des, img.shape  # (kp, des, (h, w))


# 向后兼容
_extract_sift = lambda *a, **kw: _extract_features(*a, method="sift", **kw)


def _safe_knn_match(des1, des2, k=2):
    """安全的 knnMatch，自动选择匹配器"""
    if des1 is None or des2 is None or len(des1) < 2 or len(des2) < 2:
        return []
    
    is_binary = des1.dtype == np.uint8
    if is_binary:
        matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    else:
        matcher = cv2.FlannBasedMatcher(dict(algorithm=1, trees=5), dict(checks=50))
    
    try:
        matches = matcher.knnMatch(des1, des2, k=k)
    except cv2.error:
        return []
    result = []
    for pair in matches:
        if len(pair) == k:
            result.append(pair)
    return result


def _match_with_flann(des1, des2, ratio=0.75, task_id=None, method="sift"):
    log_match_step(f"🔗 匹配: des1={des1.shape if des1 is not None else None}, des2={des2.shape if des2 is not None else None}, method={method}", task_id)
    knn = _safe_knn_match(des1, des2, k=2)
    good = []
    for pair in knn:
        m, n = pair[0], pair[1]
        if m.distance < ratio * n.distance:
            good.append(m)
    log_match_step(f"🔗 FLANN匹配: 初始匹配={len(knn)}, Lowe比率={ratio}, 通过={len(good)}", task_id)
    return good


def _load_coord_map(path="projections/coord_map.json"):
    with open(path) as f:
        data = json.load(f)
    coord_map = {}
    for key, val in data["pixels"].items():
        if "," in key:
            x_str, y_str = key.split(",")
            coord_map[(int(x_str), int(y_str))] = val
        elif "(" in key:
            x_str, y_str = key.strip("()").split(", ")
            coord_map[(int(x_str), int(y_str))] = val
    return coord_map


def _verify_with_las_points(matched_coords, las_path="las/default_2026-05-28-112428.las",
                             sample_size=5000, tol=3.0):
    try:
        from laspy import open as las_open
        reader = las_open(las_path)
        step = max(1, reader.header.point_count // sample_size)
        pts = reader.read()
        las_xyz = np.column_stack([
            pts.x[::step], pts.y[::step], pts.z[::step]
        ])
        results = []
        coords = np.array(matched_coords, dtype=np.float64)
        for c in coords:
            dists = np.sqrt(np.sum((las_xyz - c) ** 2, axis=1))
            min_dist = dists.min()
            min_idx = dists.argmin()
            results.append({
                "matched_xyz": c.tolist(),
                "nearest_las_xyz": las_xyz[min_idx].tolist(),
                "distance_m": float(min_dist),
                "verified": bool(min_dist < tol),
            })
        verified_count = sum(1 for r in results if r["verified"])
        return {
            "sample_size": sample_size,
            "total_verified": verified_count,
            "total_checked": len(results),
            "verification_rate": float(verified_count / len(results) if results else 0),
            "mean_distance_m": float(np.mean([r["distance_m"] for r in results])),
            "details": results[:10],
        }
    except Exception as e:
        return {"error": str(e)}


def _try_ransac(q_pts, p_pts, total_matches=0, task_id=None):
    """尝试多种 RANSAC 方法，返回最佳结果"""
    methods = [
        ("USAC_MAGSAC", cv2.USAC_MAGSAC, 8.0, 0.99),
        ("USAC_ACCURATE", cv2.USAC_ACCURATE, 6.0, 0.99),
        ("RANSAC", cv2.RANSAC, 5.0, 0.995),
    ]
    best_H, best_mask, best_inliers = None, None, 0
    best_name = "None"
    for name, method, threshold, confidence in methods:
        try:
            H, mask = cv2.findHomography(q_pts, p_pts, method, threshold,
                                          maxIters=5000, confidence=confidence)
            if H is not None and mask is not None:
                inliers = int(mask.sum())
                log_match_step(f"🔄 RANSAC({name}): 阈值={threshold}, 内点={inliers}/{total_matches} ({inliers/max(total_matches,1)*100:.0f}%)", task_id)
                if inliers > best_inliers:
                    best_H, best_mask, best_inliers = H, mask, inliers
                    best_name = name
        except cv2.error as e:
            log_match_step(f"⚠️ RANSAC({name}) 失败: {e}", task_id)
            continue
    if best_H is not None:
        log_match_step(f"✅ RANSAC最佳: {best_name}, 内点={best_inliers}/{total_matches}", task_id)
    else:
        log_match_step(f"❌ 所有RANSAC方法均失败", task_id)
    return best_H, best_mask


def _match_on_tile(q_kp, q_des, q_w, q_h, tile_info, task_id, feature_method="sift"):
    """在单个 tile 上执行匹配"""
    tile_path = tile_info.get("image_path", "")
    if not tile_path or not os.path.exists(tile_path):
        tile_path = str(Path("projections/tiles") / Path(tile_path).name)

    coord_path = tile_info.get("coord_map_path", "")
    if not coord_path or not os.path.exists(coord_path):
        coord_path = str(Path("projections/tiles") / Path(coord_path).name)

    p_kp, p_des, p_shape = _extract_features(tile_path, feature_method, task_id)
    if p_des is None or len(p_des) < 4:
        return None

    good = _match_with_flann(q_des, p_des, task_id=task_id, method=feature_method)
    if len(good) < 4:
        return None

    q_pts = np.float32([q_kp[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    p_pts = np.float32([p_kp[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    H, mask = _try_ransac(q_pts, p_pts, len(good), task_id)

    if H is None or mask is None:
        inlier_matches = sorted(good, key=lambda m: m.distance)[:min(15, len(good))]
    else:
        inlier_mask = mask.ravel().tolist()
        inlier_matches = [m for m, is_in in zip(good, inlier_mask) if is_in]
        if len(inlier_matches) < 3:
            inlier_matches = sorted(good, key=lambda m: m.distance)[:min(12, len(good))]

    if len(inlier_matches) < 3:
        return None

    coord_map = _load_coord_map(coord_path)
    tile_w, tile_h = tile_info["width"], tile_info["height"]

    matched_3d = []
    used_pp = set()
    used_qp = set()
    for m in inlier_matches:
        qx = int(round(q_kp[m.queryIdx].pt[0]))
        qy = int(round(q_kp[m.queryIdx].pt[1]))
        if qx < 0 or qx >= q_w or qy < 0 or qy >= q_h:
            continue
        if (qx, qy) in used_qp:
            continue
        px = int(round(p_kp[m.trainIdx].pt[0]))
        py = int(round(p_kp[m.trainIdx].pt[1]))
        if (px, py) in used_pp:
            continue
        found = False
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                c = coord_map.get((px + dx, py + dy))
                if c:
                    matched_3d.append({
                        "query_pt": (float(qx), float(qy)),
                        "proj_pt": (int(px), int(py)),
                        "point3d": c[:3],
                        "distance": float(m.distance),
                    })
                    used_pp.add((px, py))
                    used_qp.add((qx, qy))
                    found = True
                    break
            if found:
                break

    if len(matched_3d) < 3:
        return None

    return matched_3d


def _build_result(matched_3d, coords, q_w, q_h, task_id, verify):
    """从匹配点构建返回结果"""
    center = np.median(coords, axis=0)
    xs, ys, zs = coords[:, 0], coords[:, 1], coords[:, 2]
    log_match_step(f"📍 3D坐标范围: X=[{xs.min():.2f},{xs.max():.2f}] Y=[{ys.min():.2f},{ys.max():.2f}] Z=[{zs.min():.2f},{zs.max():.2f}]", task_id)
    log_match_step(f"📍 3D中心: ({center[0]:.2f}, {center[1]:.2f}, {center[2]:.2f})", task_id)

    matched_3d.sort(key=lambda m: m["distance"])
    for i, p in enumerate(matched_3d):
        log_match_step(f"  🔵 点#{i+1}: 像素({p['query_pt'][0]:.0f},{p['query_pt'][1]:.0f}) "
                       f"→ 投影({p['proj_pt'][0]},{p['proj_pt'][1]}) "
                       f"→ 3D({p['point3d'][0]:.2f},{p['point3d'][1]:.2f},{p['point3d'][2]:.2f}) "
                       f"距离={p['distance']:.1f}", task_id)

    verification = None
    if verify:
        verification = _verify_with_las_points([m["point3d"] for m in matched_3d])
        if verification and "error" not in verification:
            log_match_step(f"✅ LAS验证: {verification['total_verified']}/{verification['total_checked']} "
                          f"通过({verification['verification_rate']*100:.0f}%), "
                          f"平均偏差={verification['mean_distance_m']:.2f}m", task_id)

    display_points = []
    grid_cols, grid_rows = 3, 2
    grid_w = q_w / grid_cols
    grid_h = q_h / grid_rows
    matched_by_grid = {}
    for p in matched_3d:
        gx = min(int(p["query_pt"][0] / grid_w), grid_cols - 1)
        gy = min(int(p["query_pt"][1] / grid_h), grid_rows - 1)
        key = gy * grid_cols + gx
        if key not in matched_by_grid or p["distance"] < matched_by_grid[key]["distance"]:
            matched_by_grid[key] = p
    for grid_idx in sorted(matched_by_grid.keys()):
        if len(display_points) >= 5:
            break
        display_points.append(matched_by_grid[grid_idx])
    if len(display_points) < 5:
        existing = {(p["query_pt"][0] // 100, p["query_pt"][1] // 100) for p in display_points}
        for p in matched_3d:
            if len(display_points) >= 5:
                break
            key = (int(p["query_pt"][0] // 100), int(p["query_pt"][1] // 100))
            if key not in existing:
                display_points.append(p)
                existing.add(key)

    matched_points = [{
        "query_pt": [float(p["query_pt"][0]), float(p["query_pt"][1])],
        "proj_pt": p["proj_pt"],
        "point3d": p["point3d"],
        "distance": float(p["distance"]),
    } for p in display_points]

    regions = [{
        "num_matches": len(matched_3d),
        "num_high_conf": len(matched_3d),
        "center_3d": [float(coords.mean(axis=0)[0]), float(coords.mean(axis=0)[1]), float(coords.mean(axis=0)[2])],
    }]

    log_match_step(f"✅ 匹配完成: 内点={len(matched_3d)}, 展示={len(matched_points)}", task_id)
    log_match_step(f"{'='*60}", task_id)

    return {
        "matched": True,
        "total_matches": len(matched_3d),
        "total_inliers": len(matched_3d),
        "center_3d": [float(center[0]), float(center[1]), float(center[2])],
        "regions": regions,
        "verification": verification,
        "matched_points": matched_points,
        "all_matched_points": [
            {"query_pt": [float(p["query_pt"][0]), float(p["query_pt"][1])],
             "point3d": p["point3d"],
             "distance": float(p["distance"])}
            for p in matched_3d
        ],
    }


def match_query_to_projection(
    query_image_path: str,
    projection_image_path: str = "projections/las_projection.jpg",
    coord_map_path: str = "projections/coord_map.json",
    verify: bool = True,
    task_id: int | None = None,
    feature_method: str = "sift",
) -> dict:
    log_match_step(f"{'='*60}", task_id)
    log_match_step(f"🚀 开始匹配 [{feature_method}]: {os.path.basename(query_image_path)}", task_id)

    q_kp, q_des, q_shape = _extract_features(query_image_path, feature_method, task_id)
    if q_des is None:
        return {"matched": False, "regions": [], "message": "Query image feature extraction failed"}
    q_h, q_w = q_shape if q_shape else (0, 0)
    log_match_step(f"📐 查询图像尺寸: {q_w}x{q_h}", task_id)

    # 加载 tile 索引
    tile_index_path = Path("projections/tile_index.json")
    if not tile_index_path.exists():
        log_match_step(f"❌ tile_index.json 不存在，请先运行预处理", task_id)
        return {"matched": False, "regions": [], "message": "tile_index.json not found, run preprocess first"}

    with open(tile_index_path) as f:
        tiles = json.load(f)
    log_match_step(f"🗺️ 加载 {len(tiles)} 个 tile", task_id)

    best_result = None
    best_count = 0

    for i, tile in enumerate(tiles):
        log_match_step(f"🔍 Tile {i+1}/{len(tiles)}: {Path(tile['image_path']).name}", task_id)
        result = _match_on_tile(q_kp, q_des, q_w, q_h, tile, task_id, feature_method)
        if result is not None and len(result) > best_count:
            best_count = len(result)
            best_result = result
            log_match_step(f"  → 当前最佳: {best_count} 内点", task_id)

    if best_result is None or best_count < 3:
        log_match_step(f"❌ 所有 tile 均未匹配成功", task_id)
        return {"matched": False, "regions": [], "message": "No matching tile found"}

    coords = np.array([m["point3d"] for m in best_result], dtype=np.float64)
    return _build_result(best_result, coords, q_w, q_h, task_id, verify)


def compute_image_area_3d(
    query_image_path: str,
    region: dict | None = None,
    task_id: int | None = None,
    feature_method: str = "sift",
) -> dict:
    return match_query_to_projection(query_image_path, task_id=task_id, feature_method=feature_method)
