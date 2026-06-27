import json
from pathlib import Path

import cv2
import numpy as np


def _extract_sift(image_path: str):
    img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None, None
    sift = cv2.SIFT_create()
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

    good = _match_with_flann(q_des, p_des)
    if len(good) < 4:
        return {"matched": False, "regions": [], "message": f"Insufficient matches ({len(good)})"}

    coord_map = _load_coord_map(coord_map_path)

    matched_3d = []
    matched_pixels = []
    for m in good:
        px = int(round(p_kp[m.trainIdx].pt[0]))
        py = int(round(p_kp[m.trainIdx].pt[1]))
        found = False
        for dx in range(-3, 4):
            for dy in range(-3, 4):
                coord = coord_map.get((px + dx, py + dy))
                if coord:
                    matched_3d.append({
                        "query_pt": (float(q_kp[m.queryIdx].pt[0]),
                                     float(q_kp[m.queryIdx].pt[1])),
                        "proj_pt": (int(px), int(py)),
                        "point3d": coord,
                        "distance": float(m.distance),
                    })
                    matched_pixels.append((px + dx, py + dy))
                    found = True
                    break
            if found:
                break

    if not matched_3d:
        return {"matched": False, "regions": [], "message": "No 3D projection matches found"}

    coords = np.array([m["point3d"] for m in matched_3d], dtype=np.float64)
    center = np.median(coords, axis=0)

    distances = [m["distance"] for m in matched_3d]
    med_dist = np.median(distances)
    high_confidence = [m for m in matched_3d if m["distance"] < med_dist * 1.5]
    h_coords = np.array([m["point3d"] for m in high_confidence], dtype=np.float64)
    h_center = np.median(h_coords, axis=0) if len(h_coords) > 0 else center

    verification = None
    if verify:
        sample_coords = [m["point3d"] for m in matched_3d]
        verification = _verify_with_las_points(sample_coords)

    regions = [{
        "name": "las_projection_match",
        "num_matches": len(matched_3d),
        "num_high_conf": len(high_confidence),
        "center_3d": [float(h_center[0]), float(h_center[1]), float(h_center[2])],
        "avg_distance": float(np.mean(distances)),
    }]

    return {
        "matched": True,
        "total_matches": len(matched_3d),
        "total_inliers": len(high_confidence),
        "center_3d": [float(center[0]), float(center[1]), float(center[2])],
        "high_conf_center_3d": [float(h_center[0]), float(h_center[1]), float(h_center[2])],
        "regions": regions,
        "verification": verification,
    }


def compute_image_area_3d(
    query_image_path: str,
    region: dict | None = None,
) -> dict:
    return match_query_to_projection(query_image_path)
