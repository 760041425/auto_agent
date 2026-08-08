"""定位算法注册和应用层分派。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class LocalizationInput:
    image_path: str
    output_dir: str
    max_iterations: int = 2
    debug_visualizations: bool = False
    camera_intrinsics: Any = None
    fov_deg: float = 75.0
    use_pose_prior: bool = False
    prior_position: tuple[float, float, float] | None = None
    prior_radius: float = 15.0
    reproj_error: float = 4.0
    min_inliers: int = 12
    geometric_verify: bool = False
    keep_aspect_ratio: bool = True
    coordinate_threshold_m: float = 0.3


Runner = Callable[[LocalizationInput], dict[str, Any]]


@dataclass(frozen=True)
class AlgorithmDefinition:
    algorithm_id: str
    label: str
    feature_method: str
    runner: Runner


class AlgorithmRegistry:
    def __init__(self, definitions: list[AlgorithmDefinition]):
        self._definitions = {definition.algorithm_id: definition for definition in definitions}

    def ids(self) -> tuple[str, ...]:
        return tuple(self._definitions)

    def get(self, algorithm_id: str) -> AlgorithmDefinition:
        try:
            return self._definitions[algorithm_id]
        except KeyError as exc:
            raise KeyError(f"unknown localization algorithm: {algorithm_id}") from exc

    def run(self, algorithm_id: str, localization_input: LocalizationInput) -> dict[str, Any]:
        return self.get(algorithm_id).runner(localization_input)


def _run_salad_v2_disk(localization_input: LocalizationInput) -> dict[str, Any]:
    return _run_salad_v2(localization_input, matcher_mode="disk_lg")


def _run_salad_v2_loftr(localization_input: LocalizationInput) -> dict[str, Any]:
    return _run_salad_v2(localization_input, matcher_mode="loftr")


def _run_salad_v2_hybrid(localization_input: LocalizationInput) -> dict[str, Any]:
    return _run_salad_v2(localization_input, matcher_mode="hybrid")


def _run_salad_v2(localization_input: LocalizationInput, matcher_mode: str) -> dict[str, Any]:
    from services.localizer.salad_roma_v2 import localize_with_salad_roma_v2

    return localize_with_salad_roma_v2(
        localization_input.image_path,
        output_dir=localization_input.output_dir,
        max_iterations=localization_input.max_iterations,
        debug_visualizations=localization_input.debug_visualizations,
        camera_intrinsics=localization_input.camera_intrinsics,
        fov_deg=localization_input.fov_deg,
        use_pose_prior=localization_input.use_pose_prior,
        prior_position=localization_input.prior_position,
        prior_radius=localization_input.prior_radius,
        reproj_error=localization_input.reproj_error,
        min_inliers=localization_input.min_inliers,
        geometric_verify=localization_input.geometric_verify,
        keep_aspect_ratio=localization_input.keep_aspect_ratio,
        coordinate_threshold_m=localization_input.coordinate_threshold_m,
        matcher_mode=matcher_mode,
    )


def _run_ace_las(localization_input: LocalizationInput) -> dict[str, Any]:
    from services.localizer.salad_roma_v2 import ace_localize_with_las_verify

    return ace_localize_with_las_verify(
        localization_input.image_path,
        camera_intrinsics=localization_input.camera_intrinsics,
        fov_deg=localization_input.fov_deg,
    )


def _run_multi_strategy(localization_input: LocalizationInput) -> dict[str, Any]:
    from services.localizer.salad_roma_v2 import localize_multi_strategy

    return localize_multi_strategy(
        localization_input.image_path,
        camera_intrinsics=localization_input.camera_intrinsics,
        fov_deg=localization_input.fov_deg,
    )


def _run_legacy(
    localization_input: LocalizationInput,
    *,
    feature_method: str,
    match_method: str,
) -> dict[str, Any]:
    from services.localizer import localize_image

    return localize_image(
        localization_input.image_path,
        output_dir=localization_input.output_dir,
        feature_method=feature_method,
        match_method=match_method,
        max_iterations=localization_input.max_iterations,
        debug_visualizations=localization_input.debug_visualizations,
        coordinate_threshold_m=localization_input.coordinate_threshold_m,
    )


def _run_salad_roma(localization_input: LocalizationInput) -> dict[str, Any]:
    return _run_legacy(localization_input, feature_method="dino", match_method="salad_roma")


def _run_salad_lightglue(localization_input: LocalizationInput) -> dict[str, Any]:
    return _run_legacy(localization_input, feature_method="dino", match_method="salad_lightglue")


def _run_ace(localization_input: LocalizationInput) -> dict[str, Any]:
    return _run_legacy(localization_input, feature_method="ace", match_method="ace")


def _run_flann(localization_input: LocalizationInput) -> dict[str, Any]:
    return _run_legacy(localization_input, feature_method="sift", match_method="flann")


_DEFAULTS = (
    ("salad_roma_v2", "SALAD v2 (DISK+LG)", "dino", _run_salad_v2_disk),
    ("salad_roma_v2_loftr", "SALAD v2 + LoFTR", "dino", _run_salad_v2_loftr),
    ("hybrid", "Hybrid (DISK+LG + LoFTR)", "dino", _run_salad_v2_hybrid),
    ("ace_las", "ACE + LAS 验证", "ace", _run_ace_las),
    ("multi_strategy", "Multi-Strategy 融合", "multi", _run_multi_strategy),
    ("salad_roma", "SALAD+RoMa", "dino", _run_salad_roma),
    ("salad_lightglue", "SALAD+LightGlue", "dino", _run_salad_lightglue),
    ("ace", "ACE 场景坐标回归", "ace", _run_ace),
    ("flann", "SIFT + FLANN", "sift", _run_flann),
)


def build_default_registry(
    runner_overrides: Mapping[str, Runner] | None = None,
) -> AlgorithmRegistry:
    overrides = dict(runner_overrides or {})
    definitions = [
        AlgorithmDefinition(algorithm_id, label, feature_method, overrides.get(algorithm_id, runner))
        for algorithm_id, label, feature_method, runner in _DEFAULTS
    ]
    return AlgorithmRegistry(definitions)


DEFAULT_ALGORITHM_REGISTRY = build_default_registry()
