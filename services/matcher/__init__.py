import json
from pathlib import Path

import cv2
import numpy as np


def _extract_sift(image_path: str):
    img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None, None
    sift = cv2.SIFT_create(nfeatures=5000)
    return sift.detectAndCompute(img, None)


def _match_with_flann(des1, des2, ratio=0.75):
    if des1 is None or des2 is None or len(des1) < 2 or len(des2) < 2:
        return []
    flann = cv2.FlannBasedMatcher(dict(algorithm=1, trees=5), dict(checks=50))
    matches = flann.knnMatch(des1, des2, k=2)
    return [m for m, n in matches if m.distance < ratio * n.distance]


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


def match_query_to_projection(
    query_image_path: str,
    projection_image_path: str = "projections/las_projection.jpg",
    coord_map_path: str = "projections/coord_map.json",
    verify: bool = True,
) -> dict:
    q_kp, q_des = _extract_sift(query_image_path)
    p_kp, p_des = _extract_sift(projection_image_path)

    if q_des is None or p_des is None:
        return {"matched": False, "regions": [], "message": "Feature extraction failed"}

    # FLANN 匹配（0.75 比率测试）
    good = _match_with_flann(q_des, p_des)
    if len(good) < 8:
        return {"matched": False, "regions": [], "message": f"Insufficient initial matches ({len(good)})"}

    # === RANSAC 几何验证（宽松阈值） ===
    q_pts = np.float32([q_kp[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    p_pts = np.float32([p_kp[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    H, mask = cv2.findHomography(q_pts, p_pts, cv2.USAC_MAGSAC, 6.0, maxIters=10000, confidence=0.99)
    if H is None or mask is None:
        return {"matched": False, "regions": [], "message": "RANSAC verification failed"}
    inlier_mask = mask.ravel().tolist()
    inlier_matches = [m for m, is_inlier in zip(good, inlier_mask) if is_inlier]

    if len(inlier_matches) < 4:
        return {"matched": False, "regions": [], "message": f"Too few inliers after RANSAC ({len(inlier_matches)})"}

    # 获取查询图像的尺寸用于边界检查
    q_img = cv2.imread(str(query_image_path), cv2.IMREAD_GRAYSCALE)
    q_h, q_w = q_img.shape[:2] if q_img is not None else (0, 0)

    coord_map = _load_coord_map(coord_map_path)

    matched_3d = []
    proj_pixel_set = set()
    query_pixel_set = set()
    for m in inlier_matches:
        qx = int(round(q_kp[m.queryIdx].pt[0]))
        qy = int(round(q_kp[m.queryIdx].pt[1]))
        # 检查查询图像上的点是否在图像范围内（排除出界点）
        if qx < 0 or qx >= q_w or qy < 0 or qy >= q_h:
            continue
        # 去重：同一个查询图像像素只取一次
        if (qx, qy) in query_pixel_set:
            continue
        px = int(round(p_kp[m.trainIdx].pt[0]))
        py = int(round(p_kp[m.trainIdx].pt[1]))
        if (px, py) in proj_pixel_set:
            continue
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
                    proj_pixel_set.add((px, py))
                    query_pixel_set.add((qx, qy))
                    found = True
                    break
            if found:
                break

    if len(matched_3d) < 4:
        return {"matched": False, "regions": [], "message": f"Too few coord-mapped matches ({len(matched_3d)})"}

    coords = np.array([m["point3d"] for m in matched_3d], dtype=np.float64)

    # 检查3D坐标离散度
    coord_std = coords.std(axis=0)
    if coord_std[0] < 0.2 and coord_std[1] < 0.2 and len(matched_3d) > 10:
        return {"matched": False, "regions": [], "message": "Matches too concentrated in 3D"}

    center = np.median(coords, axis=0)

    # 按匹配距离排序
    matched_3d.sort(key=lambda m: m["distance"])

    verification = None
    if verify:
        sample_coords = [m["point3d"] for m in matched_3d]
        verification = _verify_with_las_points(sample_coords)

    # 选取展示用的点：在图像上均匀分布
    display_points = []
    # 把图像分成 3x2 网格，每格取一个最佳点
    grid_cols = 3
    grid_rows = 2
    grid_w = q_w / grid_cols
    grid_h = q_h / grid_rows
    matched_by_grid = {}
    for p in matched_3d:
        gx = min(int(p["query_pt"][0] / grid_w), grid_cols - 1)
        gy = min(int(p["query_pt"][1] / grid_h), grid_rows - 1)
        key = (gy * grid_cols + gx)
        if key not in matched_by_grid or p["distance"] < matched_by_grid[key]["distance"]:
            matched_by_grid[key] = p

    # 按网格顺序取点，保证覆盖不同区域
    for grid_idx in sorted(matched_by_grid.keys()):
        if len(display_points) >= 5:
            break
        p = matched_by_grid[grid_idx]
        display_points.append(p)

    # 如果网格法取不够5个，补距离最近的
    if len(display_points) < 5:
        existing_keys = {(p["query_pt"][0] // 100, p["query_pt"][1] // 100) for p in display_points}
        for p in matched_3d:
            if len(display_points) >= 5:
                break
            key = (int(p["query_pt"][0] // 100), int(p["query_pt"][1] // 100))
            if key not in existing_keys:
                display_points.append(p)
                existing_keys.add(key)

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
            {
                "query_pt": [float(p["query_pt"][0]), float(p["query_pt"][1])],
                "point3d": p["point3d"],
                "distance": float(p["distance"]),
            }
            for p in matched_3d
        ],
    }


def compute_image_area_3d(
    query_image_path: str,
    region: dict | None = None,
) -> dict:
    return match_query_to_projection(query_image_path)
