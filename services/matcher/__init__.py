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
        x, y = key.strip("()").split(", ")
        coord_map[(int(x), int(y))] = val
    return coord_map


def match_query_to_projection(
    query_image_path: str,
    projection_image_path: str = "projections/las_projection.jpg",
    coord_map_path: str = "projections/coord_map.json",
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
    for m in good:
        px = int(round(p_kp[m.trainIdx].pt[0]))
        py = int(round(p_kp[m.trainIdx].pt[1]))
        search_radius = 3
        found = False
        for dx in range(-search_radius, search_radius + 1):
            for dy in range(-search_radius, search_radius + 1):
                coord = coord_map.get((px + dx, py + dy))
                if coord:
                    matched_3d.append({
                        "query_pt": (float(q_kp[m.queryIdx].pt[0]), float(q_kp[m.queryIdx].pt[1])),
                        "proj_pt": (int(px), int(py)),
                        "point3d": coord,
                        "distance": float(m.distance),
                    })
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
    if high_confidence:
        high_coords = np.array([m["point3d"] for m in high_confidence], dtype=np.float64)
        h_center = np.median(high_coords, axis=0)
    else:
        h_center = center

    regions = [{
        "name": "primary_match",
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
    }


def compute_image_area_3d(
    query_image_path: str,
    region: dict | None = None,
) -> dict:
    return match_query_to_projection(query_image_path)
