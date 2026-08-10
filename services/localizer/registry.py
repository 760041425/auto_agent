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


# ── 新方案：空间特征定位 ──

def _run_pointcloud_descriptor(localization_input: LocalizationInput) -> dict[str, Any]:
    from services.localizer.spatial_localizers import localize_pointcloud_descriptor
    return localize_pointcloud_descriptor(
        localization_input.image_path,
        fov_deg=localization_input.fov_deg,
    )


def _run_depth_icp(localization_input: LocalizationInput) -> dict[str, Any]:
    from services.localizer.spatial_localizers import localize_depth_icp
    return localize_depth_icp(
        localization_input.image_path,
        fov_deg=localization_input.fov_deg,
    )


def _run_explicit_2d3d(localization_input: LocalizationInput) -> dict[str, Any]:
    from services.localizer.spatial_localizers import localize_explicit_2d3d
    return localize_explicit_2d3d(
        localization_input.image_path,
        fov_deg=localization_input.fov_deg,
    )


def _run_render_compare(localization_input: LocalizationInput) -> dict[str, Any]:
    from services.localizer.spatial_localizers import localize_render_compare
    return localize_render_compare(
        localization_input.image_path,
        fov_deg=localization_input.fov_deg,
    )


def _run_fast(localization_input: LocalizationInput) -> dict[str, Any]:
    from services.localizer.fast_localizer import localize_fast
    return localize_fast(
        localization_input.image_path,
        fov_deg=localization_input.fov_deg,
    )


def _run_ultrafast(localization_input: LocalizationInput) -> dict[str, Any]:
    from services.localizer.ultrafast_localizer import localize_ultrafast
    return localize_ultrafast(
        localization_input.image_path,
        fov_deg=localization_input.fov_deg,
    )


def _run_ace_normal(localization_input: LocalizationInput) -> dict[str, Any]:
    from services.localizer.ace_localizer import ace_with_normal
    return ace_with_normal(
        localization_input.image_path,
        fov_deg=localization_input.fov_deg,
    )


def _run_ace_rgb(localization_input: LocalizationInput) -> dict[str, Any]:
    from services.localizer.ace_localizer import ace_rgb_only
    return ace_rgb_only(
        localization_input.image_path,
        fov_deg=localization_input.fov_deg,
    )


def _run_depth_icp(localization_input: LocalizationInput) -> dict[str, Any]:
    from services.localizer.ace_localizer import depth_icp
    return depth_icp(
        localization_input.image_path,
        fov_deg=localization_input.fov_deg,
    )


def _run_ace_better_normal(localization_input: LocalizationInput) -> dict[str, Any]:
    from services.localizer.enhanced_ace import ace_with_better_normal
    return ace_with_better_normal(
        localization_input.image_path,
        fov_deg=localization_input.fov_deg,
    )


def _run_depth_anything(localization_input: LocalizationInput) -> dict[str, Any]:
    from services.localizer.enhanced_ace import depth_anything_icp
    return depth_anything_icp(
        localization_input.image_path,
        fov_deg=localization_input.fov_deg,
    )


def _finalize_ace_result(
    result: dict[str, Any],
    *,
    algorithm_id: str = "train_ace",
    min_inliers: int = 12,
    elapsed_s: float | None = None,
    feature_method: str = "ace",
) -> dict[str, Any]:
    """训练 ACE 完成后的结果归一化出口，与 api.routes.localize._append_result 等价。"""
    from services.localizer.contracts import normalize_localization_result

    return normalize_localization_result(
        algorithm_id,
        result,
        min_inliers=min_inliers,
        elapsed_s=elapsed_s,
        feature_method=feature_method,
    )


def _run_train_ace(localization_input: LocalizationInput) -> dict[str, Any]:
    """训练 ACE 并自动使用新模型定位（后台异步）"""
    import threading
    import time
    from api.database import SessionLocal
    from api.models import TaskModel

    tag = "ace_trained"
    task_id = localization_input.__dict__.get('_task_id')  # 获取任务 ID

    # 后台训练 + 定位
    def _train_and_localize():
        try:
            from services.localizer.enhanced_ace import train_ace_on_scene
            from services.localizer.ace_localizer import ace_rgb_only

            # 训练（10 epochs，约 6 分钟）
            model_path = train_ace_on_scene(epochs=10)

            # 定位
            started_at = time.perf_counter()
            result = ace_rgb_only(
                localization_input.image_path,
                fov_deg=localization_input.fov_deg,
                model_path=model_path,
            )
            elapsed_s = time.perf_counter() - started_at

            # 更新任务状态
            db = SessionLocal()
            task = db.query(TaskModel).filter(TaskModel.id == task_id).first()
            if task:
                normalized = _finalize_ace_result(
                    result,
                    algorithm_id="train_ace",
                    min_inliers=localization_input.min_inliers,
                    elapsed_s=elapsed_s,
                    feature_method="ace",
                )
                task.result_json = {"results": [normalized], "total": 1}
                task.status = "completed" if normalized.get("success") else "failed"
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(task, "result_json")
                db.commit()
            db.close()
        except Exception as e:
            db = SessionLocal()
            task = db.query(TaskModel).filter(TaskModel.id == task_id).first()
            if task:
                task.status = "failed"
                task.error_message = str(e)[:200]
                db.commit()
            db.close()

    # 启动后台线程
    thread = threading.Thread(target=_train_and_localize, daemon=True)
    thread.start()

    # 立即返回"训练中"状态
    return {
        "success": True,
        "tag": tag,
        "status": "training",
        "message": "ACE 训练已启动（约 6 分钟），请稍后刷新查看结果",
        "spatial_config": {"method": "ace_trained_on_scene", "epochs": 10},
        "elapsed": 0,
    }


_DEFAULTS = (
    # 原有方案
    ("salad_roma_v2", "SALAD v2 (DISK+LG)", "dino", _run_salad_v2_disk),
    ("salad_roma_v2_loftr", "SALAD v2 + LoFTR", "dino", _run_salad_v2_loftr),
    ("hybrid", "Hybrid (DISK+LG + LoFTR)", "dino", _run_salad_v2_hybrid),
    ("ace_las", "ACE + LAS 验证", "ace", _run_ace_las),
    ("multi_strategy", "Multi-Strategy 融合", "multi", _run_multi_strategy),
    ("salad_roma", "SALAD+RoMa", "dino", _run_salad_roma),
    ("salad_lightglue", "SALAD+LightGlue", "dino", _run_salad_lightglue),
    ("ace", "ACE 场景坐标回归", "ace", _run_ace),
    ("flann", "SIFT + FLANN", "sift", _run_flann),
    # 新方案：空间特征
    ("pointcloud_descriptor", "A. 点云全局描述子", "spatial", _run_pointcloud_descriptor),
    ("depth_icp", "B. 深度估计+ICP", "spatial", _run_depth_icp),
    ("explicit_2d3d", "C. 显式2D-3D匹配", "spatial", _run_explicit_2d3d),
    ("render_compare", "D. 渲染对比定位", "spatial", _run_render_compare),
    # 快速版
    ("fast", "E. 快速定位 (~40s)", "fast", _run_fast),
    ("ultrafast", "F. 超快定位 (~31s)", "fast", _run_ultrafast),
    # ACE 场景坐标回归
    ("ace_normal", "G. ACE+法线估计 (~2s)", "ace", _run_ace_normal),
    ("ace_rgb", "H. ACE RGB-only (~2s)", "ace", _run_ace_rgb),
    ("depth_icp", "I. 深度+ICP", "depth", _run_depth_icp),
    # 增强版 ACE
    ("ace_better", "J. ACE+更好法线 (~3s)", "ace", _run_ace_better_normal),
    ("depth_anything", "K. DepthAnything+ICP", "depth", _run_depth_anything),
    ("train_ace", "L. 训练ACE+定位", "ace", _run_train_ace),
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
