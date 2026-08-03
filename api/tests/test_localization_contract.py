from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from api.routes.localize import (
    CoordinateTransformRequest,
    LocalizeRequest,
    _algorithm_id_to_matcher_type,
    _append_result,
    coordinate_transform,
    _normalize_algorithms,
    _public_artifact_path,
)
from services.localizer.contracts import normalize_localization_result
from services.localizer.registry import LocalizationInput, build_default_registry


NEW_ALGORITHMS = {
    "salad_roma_v2": "disk_lg",
    "salad_roma_v2_loftr": "loftr",
    "hybrid": "hybrid",
    "ace_las": "ace_las",
    "multi_strategy": "multi_strategy",
}


@pytest.mark.parametrize("algorithm_id, expected_mode", NEW_ALGORITHMS.items())
def test_registry_dispatches_each_public_algorithm_to_its_runner(algorithm_id, expected_mode):
    """TL-003-01: 稳定算法 ID 必须精确调用其注册 runner。"""
    calls = []

    def runner(localization_input):
        calls.append(localization_input)
        return {"success": True, "mode": expected_mode, "inliers": 12}

    registry = build_default_registry(runner_overrides={algorithm_id: runner})
    request = LocalizationInput(image_path="query.jpg", output_dir="out", min_inliers=10)

    result = registry.run(algorithm_id, request)

    assert result["mode"] == expected_mode
    assert calls == [request]


def test_registry_rejects_unknown_algorithm():
    """TL-003-02: 未知算法不能静默回退到旧定位流程。"""
    registry = build_default_registry()

    with pytest.raises(KeyError, match="unknown"):
        registry.run("unknown", LocalizationInput(image_path="query.jpg", output_dir="out"))


@pytest.mark.parametrize(
    "algorithm_id, expected_mode",
    [
        ("salad_roma_v2", "disk_lg"),
        ("salad_roma_v2_loftr", "loftr"),
        ("hybrid", "hybrid"),
    ],
)
def test_default_registry_passes_the_expected_v2_matcher_mode(
    monkeypatch, algorithm_id, expected_mode
):
    """TL-003-01: 默认注册表不能只在测试覆盖表中看起来正确。"""
    from services.localizer import salad_roma_v2

    captured = {}

    def fake_v2(image_path, **kwargs):
        captured.update(kwargs)
        return {"success": True, "inliers": 12}

    monkeypatch.setattr(salad_roma_v2, "localize_with_salad_roma_v2", fake_v2)
    registry = build_default_registry()

    registry.run(algorithm_id, LocalizationInput(image_path="query.jpg", output_dir="out"))

    assert captured["matcher_mode"] == expected_mode


def test_runtime_directories_are_created_for_a_clean_checkout(tmp_path):
    """TL-003-15: 静态目录不能依赖本地历史运行产物。"""
    from api.runtime import ensure_runtime_directories

    paths = ensure_runtime_directories(tmp_path)

    assert {path.name for path in paths} == {"query_images", "projections", "reports", "logs"}
    assert all(path.is_dir() for path in paths)


def test_absolute_artifact_path_is_mapped_to_public_projection_url(tmp_path):
    artifact = tmp_path / "projections/localize/compare.jpg"

    assert _public_artifact_path(artifact) == "/projections/localize/compare.jpg"


def test_append_result_maps_all_visual_artifacts_to_public_urls():
    """TL-003-20: 查询图、最终投影和双图都必须经过 API URL 适配。"""
    results = []

    _append_result(
        results,
        "salad_roma_v2_loftr",
        {
            "success": True,
            "inliers": 12,
            "query_image": "projections/localize/query.png",
            "reprojection_image": "projections/localize/reprojection.png",
            "comparison_image": "projections/localize/comparison.png",
            "artifact_generation": {"status": "available", "error": None},
        },
        min_inliers=12,
    )

    assert results[0]["artifacts"] == {
        "query_image": "/projections/localize/query.png",
        "reprojection_image": "/projections/localize/reprojection.png",
        "comparison_image": "/projections/localize/comparison.png",
    }


def test_localize_request_rejects_malformed_camera_intrinsics():
    """TL-003-02: 相机内参必须是 3x3 或长度为 9 的数组。"""
    with pytest.raises(ValidationError):
        LocalizeRequest(image_id=1, algorithms=["salad_roma_v2"], camera_intrinsics=[1, 2, 3])


def test_localize_request_defaults_to_strict_coordinate_threshold():
    request = LocalizeRequest(image_id=1, algorithms=["salad_roma_v2_loftr"])

    assert request.coordinate_threshold_m == 0.3


def test_normalize_result_preserves_contract_and_marks_low_inliers_unreliable():
    """TL-003-03/TL-003-05: 求解成功与可信结果使用不同字段表达。"""
    raw = {
        "success": True,
        "pose": {"translation": [1.0, 2.0, 3.0]},
        "inliers": 5,
        "match_count": 30,
        "reprojection_error": 2.5,
        "projection_verification": {
            "metric_type": "projection_consistency",
            "status": "available",
            "median_m": 1.2,
        },
        "las_verification": {"verification_rate": 0.8},
        "comparison_image": "projections/localize/compare.jpg",
        "query_image": "projections/localize/query.jpg",
        "reprojection_image": "projections/localize/reprojection.jpg",
        "artifact_generation": {"status": "available", "error": None},
        "coordinate_transform": {
            "status": "ready",
            "homography": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
            "projection_npy": "projections/localize/projection_xyz.npy",
            "width": 512,
            "height": 512,
        },
    }

    result = normalize_localization_result(
        "salad_roma_v2", raw, min_inliers=12, elapsed_s=1.25
    )

    assert result["algorithm_id"] == "salad_roma_v2"
    assert result["success"] is True
    assert result["reliable"] is False
    assert result["quality"] == {
        "match_count": 30,
        "inlier_count": 5,
        "reprojection_error_px": 2.5,
    }
    assert result["validations"]["projection_consistency"]["median_m"] == 1.2
    assert result["validations"]["las_nearest"]["verification_rate"] == 0.8
    assert result["validations"]["ground_truth"]["status"] == "not_available"
    assert result["artifacts"]["comparison_image"].endswith("compare.jpg")
    assert result["artifacts"]["query_image"].endswith("query.jpg")
    assert result["artifacts"]["reprojection_image"].endswith("reprojection.jpg")
    assert result["validations"]["artifact_generation"]["status"] == "available"
    assert result["coordinate_transform"]["status"] == "ready"
    assert result["timings"]["total_s"] == 1.25
    assert result["error"] is None
    # 一个发布周期内保留旧前端字段。
    assert result["inliers"] == 5
    assert result["projection_verification"]["median_m"] == 1.2


def test_normalize_result_marks_threshold_result_reliable():
    """TL-003-05: 达到质量门槛时才能标为可信。"""
    result = normalize_localization_result(
        "salad_roma_v2",
        {"success": True, "inliers": 12},
        min_inliers=12,
    )

    assert result["success"] is True
    assert result["reliable"] is True


def test_normalize_result_respects_coordinate_difference_reliability_over_inliers():
    """TL-003-29: 即使内点很多，坐标差未通过也必须是低可信。"""
    result = normalize_localization_result(
        "salad_roma_v2_loftr",
        {
            "success": True,
            "reliable": False,
            "inliers": 100,
            "coordinate_transform": {
                "status": "ready",
                "consistency": {
                    "status": "available",
                    "median_m": 8.0,
                    "threshold_m": 3.0,
                    "passed": False,
                },
            },
        },
        min_inliers=12,
    )

    assert result["reliable"] is False
    assert result["coordinate_transform"]["consistency"]["median_m"] == 8.0


def test_normalize_result_keeps_structured_failure_reason():
    """TL-003-04: 算法失败不能在 API 适配层被吞掉。"""
    result = normalize_localization_result(
        "hybrid",
        {"success": False, "error": "model unavailable"},
        min_inliers=12,
    )

    assert result["success"] is False
    assert result["reliable"] is False
    assert result["error"] == {
        "code": "localization_failed",
        "message": "model unavailable",
    }


def test_normalize_algorithms_rejects_unknown_id():
    """TL-003-02: API 请求校验仍以注册的稳定 ID 为准。"""
    req = LocalizeRequest(image_id=1, algorithms=["unknown"])

    with pytest.raises(Exception) as exc_info:
        _normalize_algorithms(req)

    assert getattr(exc_info.value, "status_code", None) == 422


def test_normalize_algorithms_rejects_empty_selection():
    """TL-003-02: 空算法列表不能创建无行为任务。"""
    req = LocalizeRequest(image_id=1, algorithms=[])

    with pytest.raises(Exception) as exc_info:
        _normalize_algorithms(req)

    assert getattr(exc_info.value, "status_code", None) == 422


def test_coordinate_transform_reads_local_context_from_task_result(monkeypatch):
    """TL-003-26: 选点必须读取对应 task/result 的本地单应与 NPY。"""
    context = {
        "status": "ready",
        "homography": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        "projection_npy": "projections/localize/task_222/projection_xyz.npy",
        "width": 512,
        "height": 512,
    }
    task = SimpleNamespace(
        id=222,
        result_json={"results": [{"coordinate_transform": context}]},
    )

    class FakeQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return task

    class FakeDb:
        def query(self, _model):
            return FakeQuery()

    captured = {}

    def fake_query(received_context, *, u, v):
        captured.update(context=received_context, u=u, v=v)
        return {"status": "available", "difference_m": 3.0}

    monkeypatch.setattr("api.routes.localize.query_local_coordinate_transform", fake_query)

    result = coordinate_transform(
        CoordinateTransformRequest(task_id=222, result_index=0, u=0.25, v=0.75),
        db=FakeDb(),
    )

    assert captured["context"] == context
    assert captured["u"] == 0.25
    assert captured["v"] == 0.75
    assert result["difference_m"] == 3.0


def test_coordinate_transform_request_rejects_non_normalized_point():
    """TL-003-26: 本地选点仍只接受 [0,1] 归一化坐标。"""
    with pytest.raises(ValidationError):
        CoordinateTransformRequest(task_id=222, result_index=0, u=1.2, v=0.5)


def test_coordinate_transform_has_no_slam_map_runtime_dependency():
    """TL-003-26: 迁移后不得保留 URL、HTTP 或 camera_id 运行依赖。"""
    source = Path("api/routes/localize.py").read_text(encoding="utf-8")

    assert "SLAM_MAP_URL" not in source
    assert "verify_projection_slam_map" not in source


# --------------------------------------------------------------------------- #
#  BUG-003-05: 精化步骤复用初始匹配器
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "algorithm_id, expected_matcher_type",
    [
        ("salad_roma", "tiny_roma"),
        ("salad_roma_v2", "lightglue"),
        ("salad_lightglue", "lightglue"),
        ("unknown_algo", "lightglue"),  # 未知算法回退 LightGlue
    ],
)
def test_algorithm_id_to_matcher_type_maps_initial_algorithm(algorithm_id, expected_matcher_type):
    """TL-003-35: API 端点从 algorithm_id 推导 matcher_type。"""
    assert _algorithm_id_to_matcher_type(algorithm_id) == expected_matcher_type


def test_refine_endpoint_derives_matcher_type_from_initial_algorithm():
    """TL-003-35: /refine 端点根据初始结果的 match_method 推导 matcher_type。

    验证 API 端点中使用的推导逻辑：从 target.get("match_method") 出发，
    SALAD+RoMa 应推导为 tiny_roma 而非默认 lightglue。
    """
    import api.routes.localize as loc

    # 模拟 SALAD+RoMa 初始结果 → 应推导为 tiny_roma
    target = {"success": True, "match_method": "salad_roma"}
    algorithm_id = target.get("match_method", target.get("algorithm_id", ""))
    matcher_type = loc._algorithm_id_to_matcher_type(algorithm_id)
    assert matcher_type == "tiny_roma"  # 关键：不再是固定的 lightglue

    # 模拟 SALAD v2 路径 → DISK+LightGlue
    target_v2 = {"success": True, "match_method": "salad_roma_v2"}
    algorithm_id_v2 = target_v2.get("match_method", target_v2.get("algorithm_id", ""))
    assert loc._algorithm_id_to_matcher_type(algorithm_id_v2) == "lightglue"

    # 未知算法回退 LightGlue
    target_unknown = {"success": True, "match_method": "future_algo"}
    algorithm_id_unknown = target_unknown.get("match_method", target_unknown.get("algorithm_id", ""))
    assert loc._algorithm_id_to_matcher_type(algorithm_id_unknown) == "lightglue"
