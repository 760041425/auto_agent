import json
from pathlib import Path

import cv2
import numpy as np


def _extract_sift(image_path: str):
    img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None, None, None
    sift = cv2.SIFT_create(nfeatures=3000)
    kp, des = sift.detectAndCompute(img, None)
    return kp, des, img.shape  # (kp, des, (h, w))


def _safe_knn_match(des1, des2, k=2):
    """安全的 knnMatch，兼容不同 OpenCV 版本"""
    if des1 is None or des2 is None or len(des1) < 2 or len(des2) < 2:
        return []
    flann = cv2.FlannBasedMatcher(dict(algorithm=1, trees=5), dict(checks=50))
    try:
        matches = flann.knnMatch(des1, des2, k=k)
    except cv2.error:
        return []
    result = []
    for pair in matches:
        if len(pair) == k:
            result.append(pair)
    return result


def _match_with_flann(des1, des2, ratio=0.75):
    knn = _safe_knn_match(des1, des2, k=2)
    good = []
    for pair in knn:
        m, n = pair[0], pair[1]
        if m.distance < ratio * n.distance:
            good.append(m)
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


def _verify_with_las_points(matched_coords, las_path="las/subsample_20260430181508.las",
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


def _try_ransac(q_pts, p_pts):
    """尝试多种 RANSAC 方法，返回最佳结果"""
    methods = [
        (cv2.USAC_MAGSAC, 8.0, 0.99),
        (cv2.USAC_ACCURATE, 6.0, 0.99),
        (cv2.RANSAC, 5.0, 0.995),
    ]
    best_H, best_mask, best_inliers = None, None, 0
    for method, threshold, confidence in methods:
        try:
            H, mask = cv2.findHomography(q_pts, p_pts, method, threshold,
                                          maxIters=5000, confidence=confidence)
            if H is not None and mask is not None:
                inliers = int(mask.sum())
                if inliers > best_inliers:
                    best_H, best_mask, best_inliers = H, mask, inliers
        except cv2.error:
            continue
    return best_H, best_mask


def match_query_to_projection(
    query_image_path: str,
    projection_image_path: str = "projections/las_projection.jpg",
    coord_map_path: str = "projections/coord_map.json",
    verify: bool = True,
) -> dict:
    q_kp, q_des, q_shape = _extract_sift(query_image_path)
    p_kp, p_des, _ = _extract_sift(projection_image_path)

    if q_des is None or p_des is None:
        return {"matched": False, "regions": [], "message": "Feature extraction failed"}
    if q_shape:
        q_h, q_w = q_shape
    else:
        q_h, q_w = 0, 0

    # FLANN 匹配
    good = _match_with_flann(q_des, p_des)
    if len(good) < 6:
        return {"matched": False, "regions": [], "message": f"Too few initial matches ({len(good)})"}

    # RANSAC 几何验证
    q_pts = np.float32([q_kp[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    p_pts = np.float32([p_kp[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    H, mask = _try_ransac(q_pts, p_pts)

    if H is None or mask is None:
        # 不用 RANSAC 过滤，直接用距离排序 + 边界检查
        inlier_matches = sorted(good, key=lambda m: m.distance)[:min(20, len(good))]
    else:
        inlier_mask = mask.ravel().tolist()
        inlier_matches = [m for m, is_inlier in zip(good, inlier_mask) if is_inlier]
        if len(inlier_matches) < 3:
            inlier_matches = sorted(good, key=lambda m: m.distance)[:min(15, len(good))]

    if len(inlier_matches) < 3:
        return {"matched": False, "regions": [], "message": f"Too few valid matches ({len(inlier_matches)})"}

    coord_map = _load_coord_map(coord_map_path)

    # 将匹配点转为 3D 坐标
    matched_3d = []
    used_proj_pixels = set()
    used_query_pixels = set()
    query_img_shape = (q_w, q_h)

    for m in inlier_matches:
        qx = int(round(q_kp[m.queryIdx].pt[0]))
        qy = int(round(q_kp[m.queryIdx].pt[1]))
        # 边界检查
        if qx < 0 or qx >= q_w or qy < 0 or qy >= q_h:
            continue
        # 查询像素去重
        if (qx, qy) in used_query_pixels:
            continue
        px = int(round(p_kp[m.trainIdx].pt[0]))
        py = int(round(p_kp[m.trainIdx].pt[1]))
        if (px, py) in used_proj_pixels:
            continue
        # 查找 3D 坐标（允许小范围邻域搜索）
        found = False
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                coord = coord_map.get((px + dx, py + dy))
                if coord:
                    matched_3d.append({
                        "query_pt": (float(qx), float(qy)),
                        "proj_pt": (int(px), int(py)),
                        "point3d": coord,
                        "distance": float(m.distance),
                    })
                    used_proj_pixels.add((px, py))
                    used_query_pixels.add((qx, qy))
                    found = True
                    break
            if found:
                break

    if len(matched_3d) < 3:
        return {"matched": False, "regions": [], "message": f"Too few 3D-mapped matches ({len(matched_3d)})"}

    coords = np.array([m["point3d"] for m in matched_3d], dtype=np.float64)
    center = np.median(coords, axis=0)

    # 按距离排序
    matched_3d.sort(key=lambda m: m["distance"])

    verification = None
    if verify:
        verification = _verify_with_las_points([m["point3d"] for m in matched_3d])

    # 选取展示点：按网格均匀分布在图像上
    display_points = []
    grid_cols = 3
    grid_rows = 2
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

    # 补足到5个
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
        "name": "las_projection_match",
        "num_matches": len(matched_3d),
        "num_high_conf": len(matched_3d),
        "center_3d": [float(coords.mean(axis=0)[0]), float(coords.mean(axis=0)[1]), float(coords.mean(axis=0)[2])],
        "avg_distance": float(np.mean([m["distance"] for m in matched_3d])),
    }]

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


def compute_image_area_3d(
    query_image_path: str,
    region: dict | None = None,
) -> dict:
    return match_query_to_projection(query_image_path)
