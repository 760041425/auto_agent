"""独立真值下的位姿误差计算。"""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def _rotation_matrix(pose: dict[str, Any]) -> np.ndarray:
    quaternion = pose.get("quaternion")
    if quaternion is not None:
        q = np.asarray(quaternion, dtype=np.float64).reshape(4)
        norm = np.linalg.norm(q)
        if norm == 0:
            raise ValueError("quaternion must not be zero")
        w, x, y, z = q / norm
        return np.array(
            [
                [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
            ],
            dtype=np.float64,
        )

    rotation_vector = np.asarray(pose.get("rotation_vector"), dtype=np.float64).reshape(3)
    angle = float(np.linalg.norm(rotation_vector))
    if angle == 0:
        return np.eye(3, dtype=np.float64)
    axis = rotation_vector / angle
    x, y, z = axis
    skew = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]], dtype=np.float64)
    return np.eye(3) + math.sin(angle) * skew + (1 - math.cos(angle)) * (skew @ skew)


def compute_pose_error(
    estimated_pose: dict[str, Any],
    reference_pose: dict[str, Any],
) -> dict[str, float | str]:
    """分别计算相机平移误差（米）和旋转误差（度）。"""
    estimated_translation = np.asarray(
        estimated_pose.get("translation"), dtype=np.float64
    ).reshape(3)
    reference_translation = np.asarray(
        reference_pose.get("translation"), dtype=np.float64
    ).reshape(3)
    translation_error = float(np.linalg.norm(estimated_translation - reference_translation))

    estimated_rotation = _rotation_matrix(estimated_pose)
    reference_rotation = _rotation_matrix(reference_pose)
    delta = estimated_rotation @ reference_rotation.T
    cosine = float(np.clip((np.trace(delta) - 1.0) / 2.0, -1.0, 1.0))
    rotation_error = math.degrees(math.acos(cosine))

    return {
        "status": "available",
        "translation_error_m": round(translation_error, 6),
        "rotation_error_deg": round(rotation_error, 6),
    }
