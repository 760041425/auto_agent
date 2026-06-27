import pickle
from pathlib import Path

import cv2
import numpy as np


def extract_sift_features(image_path: str) -> tuple[list[cv2.KeyPoint], np.ndarray]:
    img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise FileNotFoundError(f"Cannot read image: {image_path}")
    sift = cv2.SIFT_create()
    kp, des = sift.detectAndCompute(img, None)
    return kp, des


def match_features(
    des1: np.ndarray, des2: np.ndarray, ratio_thresh: float = 0.75
) -> list[cv2.DMatch]:
    if des1 is None or des2 is None:
        return []
    flann = cv2.FlannBasedMatcher(
        dict(algorithm=1, trees=5), dict(checks=50)
    )
    matches = flann.knnMatch(des1, des2, k=2)
    good = [m for m, n in matches if m.distance < ratio_thresh * n.distance]
    return good


def build_reference_index(
    reference_dir: str,
    image_names: list[str],
    cache_path: str = "projections/features.pkl",
    force: bool = False,
):
    cache = Path(cache_path)
    if cache.exists() and not force:
        with open(cache, "rb") as f:
            return pickle.load(f)

    reference_dir = Path(reference_dir)
    features = {}
    for name in image_names:
        img_path = reference_dir / name
        if img_path.exists():
            kp, des = extract_sift_features(str(img_path))
            features[name] = {
                "keypoints": [(p.pt, p.size, p.angle, p.response) for p in kp],
                "descriptors": des,
            }
    cache.parent.mkdir(parents=True, exist_ok=True)
    with open(cache, "wb") as f:
        pickle.dump(features, f)
    return features
