"""hloc 官方基线的轻量适配与依赖状态。"""

from __future__ import annotations

import importlib
import importlib.util
from importlib import metadata
import platform
from pathlib import Path
import sys
import types
from typing import Any


_MODULES = ("hloc", "pycolmap", "lightglue")
_FRONTEND_MODELS: tuple[Any, Any, Any] | None = None


def _module_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        module = importlib.import_module(name)
        return str(getattr(module, "__version__", "unknown"))


def _bundles_openmp(module_name: str) -> bool:
    spec = importlib.util.find_spec(module_name)
    if spec is None:
        return False
    roots = list(spec.submodule_search_locations or ())
    if spec.origin:
        roots.append(str(Path(spec.origin).parent))
    return any(Path(root).is_dir() and next(Path(root).rglob("libomp.dylib"), None)
               for root in roots)


def load_hloc_frontend_classes() -> tuple[type, type]:
    """只加载 hloc 选定的 SuperPoint+LightGlue 前端，绕开可选提取器。"""
    if "lightglue" not in sys.modules:
        spec = importlib.util.find_spec("lightglue")
        if spec is None or not spec.submodule_search_locations:
            raise ImportError("lightglue is not installed")
        root = Path(next(iter(spec.submodule_search_locations)))
        package = types.ModuleType("lightglue")
        package.__file__ = str(root / "__init__.py")
        package.__package__ = "lightglue"
        package.__path__ = [str(root)]
        package.__spec__ = spec
        package.__loader__ = spec.loader
        sys.modules["lightglue"] = package

    superpoint = importlib.import_module("lightglue.superpoint").SuperPoint
    lightglue = importlib.import_module("lightglue.lightglue").LightGlue
    return superpoint, lightglue


def unpack_hloc_matches(
    features0: dict[str, Any],
    features1: dict[str, Any],
    matches: dict[str, Any],
) -> tuple[Any, Any, Any]:
    """把 LightGlue 批次输出转换为项目使用的 query、tile、score 数组。"""
    pair_indices = matches["matches"][0]
    scores = matches["scores"][0]
    query = features0["keypoints"][0][pair_indices[:, 0]]
    tile = features1["keypoints"][0][pair_indices[:, 1]]
    return (
        query.detach().cpu().numpy(),
        tile.detach().cpu().numpy(),
        scores.detach().cpu().numpy(),
    )


def _get_frontend_models(max_keypoints: int = 2048) -> tuple[Any, Any, Any]:
    global _FRONTEND_MODELS
    if _FRONTEND_MODELS is not None:
        return _FRONTEND_MODELS

    import torch

    superpoint_class, lightglue_class = load_hloc_frontend_classes()
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    previous_hub_dir = torch.hub.get_dir()
    model_hub_dir = Path("projections/model_cache/torch/hub").resolve()
    model_hub_dir.mkdir(parents=True, exist_ok=True)
    torch.hub.set_dir(str(model_hub_dir))
    try:
        extractor = superpoint_class(max_num_keypoints=max_keypoints).eval().to(device)
        matcher = lightglue_class(features="superpoint").eval().to(device)
    finally:
        torch.hub.set_dir(previous_hub_dir)
    _FRONTEND_MODELS = extractor, matcher, device
    return _FRONTEND_MODELS


def match_hloc_frontend(
    query_bgr: Any,
    tile_bgr: Any,
    *,
    resize: int = 512,
) -> tuple[Any, Any, Any]:
    """运行 hloc 推荐的官方 SuperPoint+LightGlue 图像对匹配。"""
    import numpy as np
    import torch

    extractor, matcher, device = _get_frontend_models()

    def to_tensor(image_bgr: Any) -> Any:
        rgb = np.ascontiguousarray(image_bgr[..., ::-1])
        tensor = torch.from_numpy(rgb.transpose(2, 0, 1)).float().div_(255.0)
        return tensor.to(device)

    with torch.inference_mode():
        features0 = extractor.extract(to_tensor(query_bgr), resize=resize)
        features1 = extractor.extract(to_tensor(tile_bgr), resize=resize)
        matches = matcher({"image0": features0, "image1": features1})
    return unpack_hloc_matches(features0, features1, matches)


def localize_with_hloc_frontend_010(
    query_image_path: str,
    *,
    exclude_query_tile_key: str | None = None,
    fov_deg: float = 75.0,
    reproj_error: float = 4.0,
    min_inliers: int = 6,
) -> dict[str, Any]:
    """SALAD 同候选 + hloc SuperPoint/LightGlue + 权威 NPY/PnP 的轻量基线。"""
    import cv2
    import numpy as np

    from services.localizer.pose_utils import (
        annotate_pnp_quality,
        get_camera_matrix,
        resize_keep_aspect,
        rotation_matrix_to_quaternion,
        solve_pnp_with_focal_search,
    )
    from services.localizer.salad_roma_v2 import (
        _build_3d_2d_matches_v2,
        _resolve_npy_path,
        _salad_retrieve_v2,
    )

    status = probe_hloc_dependencies()
    if status["frontend_status"] != "available":
        return {"success": False, "status": "skipped", "dependency_status": status}

    query = cv2.imread(query_image_path)
    if query is None:
        return {"success": False, "error": "Cannot read query image"}
    height, width = query.shape[:2]
    query_small, scale, pad = resize_keep_aspect(query, target_size=512)

    camera = get_camera_matrix(width, height, fov_deg=fov_deg)
    camera[0] *= scale
    camera[1] *= scale
    camera[0, 2] = camera[0, 2] + pad[0]
    camera[1, 2] = camera[1, 2] + pad[1]

    retrieved = _salad_retrieve_v2(
        query_small,
        top_k=1,
        use_faiss=False,
        excluded_tile_keys={exclude_query_tile_key} if exclude_query_tile_key else None,
    )
    if not retrieved:
        return {"success": False, "error": "SALAD retrieval returned no candidate"}
    tile_key, similarity, tile = retrieved[0]
    tile_image = cv2.imread(tile.get("image_path", ""))
    npy_path = _resolve_npy_path(tile, tile_key)
    if tile_image is None or npy_path is None:
        return {"success": False, "error": "Candidate tile artifact unavailable"}

    query_points, tile_points, scores = match_hloc_frontend(query_small, tile_image)
    coordinate_map = np.load(npy_path)
    object_points, image_points = _build_3d_2d_matches_v2(
        query_points,
        tile_points,
        scores,
        coordinate_map,
        min_cert=0.0,
    )
    if len(object_points) < 4:
        return {
            "success": False,
            "error": "Insufficient hloc 3D-2D correspondences",
            "match_count": len(query_points),
            "correspondence_count": len(object_points),
            "retrieved_tile_key": tile_key,
        }

    h_small, w_small = query_small.shape[:2]
    pnp = solve_pnp_with_focal_search(
        object_points,
        image_points,
        w_small,
        h_small,
        initial_K=camera,
        fov_deg=fov_deg,
        reproj_error=reproj_error,
        min_inliers=min_inliers,
    )
    pnp = annotate_pnp_quality(pnp, min_score=4.0, min_inliers=min_inliers)
    if not pnp.get("success"):
        return {
            "success": False,
            "error": "hloc frontend PnP failed",
            "match_count": len(query_points),
            "correspondence_count": len(object_points),
            "retrieved_tile_key": tile_key,
        }

    rotation = cv2.Rodrigues(pnp["rvec"])[0]
    quaternion = rotation_matrix_to_quaternion(rotation)
    translation = pnp["tvec"].reshape(3)
    return {
        "success": True,
        "tag": "hloc_superpoint_lightglue_010",
        "pose": {
            "quaternion": [float(value) for value in quaternion],
            "translation": [float(value) for value in translation],
            "rotation_vector": pnp["rvec"].reshape(3).tolist(),
        },
        "quality_passed": pnp.get("quality_passed"),
        "quality_score": pnp.get("quality_score"),
        "quality_reasons": pnp.get("quality_reasons", []),
        "inliers": int(pnp.get("inlier_count", 0)),
        "reprojection_error": float(pnp.get("reproj_error_px", float("inf"))),
        "match_count": len(query_points),
        "correspondence_count": len(object_points),
        "retrieved_tile_key": tile_key,
        "retrieval_similarity": float(similarity),
        "dependency_status": status,
    }


def probe_hloc_dependencies() -> dict[str, Any]:
    """返回可审计状态；依赖不足时明确 skipped，不做算法回退。"""
    missing = [name for name in _MODULES if importlib.util.find_spec(name) is None]
    if missing:
        return {
            "status": "skipped",
            "reason": "missing_official_hloc_dependencies",
            "missing": missing,
            "versions": {},
            "frontend_status": "skipped",
            "full_pipeline_status": "skipped",
            "full_pipeline_reason": "missing_official_hloc_dependencies",
            "unsafe_workaround_used": False,
        }
    duplicate_openmp = (
        platform.system() == "Darwin"
        and _bundles_openmp("torch")
        and _bundles_openmp("pycolmap")
    )
    return {
        "status": "available",
        "reason": None,
        "missing": [],
        "versions": {name: _module_version(name) for name in _MODULES},
        "frontend_status": "available",
        "full_pipeline_status": "skipped" if duplicate_openmp else "available",
        "full_pipeline_reason": "duplicate_openmp_runtime" if duplicate_openmp else None,
        "unsafe_workaround_used": False,
    }
