from __future__ import annotations

from types import SimpleNamespace

from scripts import benchmark_localizers


def test_manifest_reuses_config_hash_but_assigns_unique_run_id(tmp_path):
    """TL-003-11: 同配置可关联，同时每次运行可唯一追踪。"""
    query = tmp_path / "query.jpg"
    query.write_bytes(b"image-placeholder")

    first = benchmark_localizers.build_manifest(
        [str(query)], ["salad_roma_v2"], seed=42, ground_truth_path=None
    )
    second = benchmark_localizers.build_manifest(
        [str(query)], ["salad_roma_v2"], seed=42, ground_truth_path=None
    )

    assert first["config_hash"] == second["config_hash"]
    assert first["run_id"] != second["run_id"]
    assert first["config"]["seed"] == 42
    assert first["config"]["queries"][0]["sha256"]
    assert "device" in first["environment"]
    assert "git_commit" in first["environment"]


def test_benchmark_uses_shared_algorithm_registry(monkeypatch, tmp_path):
    """TASK-003-03: benchmark 与 API 必须复用同一注册 runner。"""
    calls = []

    class FakeRegistry:
        def get(self, algorithm_id):
            return SimpleNamespace(feature_method="dino")

        def run(self, algorithm_id, localization_input):
            calls.append((algorithm_id, localization_input.image_path))
            return {"success": True, "inliers": 12}

    monkeypatch.setattr(benchmark_localizers, "DEFAULT_ALGORITHM_REGISTRY", FakeRegistry())
    query = tmp_path / "query.jpg"

    result = benchmark_localizers.run_single(str(query), "salad_roma_v2")

    assert calls == [("salad_roma_v2", str(query))]
    assert result["reliable"] is True


def test_report_exposes_evidence_gap_without_ground_truth(tmp_path):
    """TL-003-12: 无真值报告不能产生最终推荐。"""
    output = tmp_path / "report.html"
    manifest = {
        "run_id": "bench-test",
        "config_hash": "abc",
        "environment": {"git_commit": "deadbeef", "device": "cpu"},
    }
    results = [
        {
            "query": "q.jpg",
            "algorithm_id": "salad_roma_v2",
            "success": True,
            "reliable": True,
            "quality": {"match_count": 20, "inlier_count": 12, "reprojection_error_px": 1.0},
            "validations": {"ground_truth": {"status": "not_available"}},
            "timings": {"total_s": 1.0},
            "error": None,
        }
    ]

    benchmark_localizers.generate_report(results, manifest, str(output))
    report = output.read_text(encoding="utf-8")

    assert "未提供独立真值" in report
    assert "方案推荐" not in report
    assert "观测汇总（非推荐）" in report


def test_uvicorn_logger_configuration_is_idempotent():
    """TL-003-14: 重复初始化不得重复增加日志 handler。"""
    from services.localizer.logger_config import configure_uvicorn_access_logger

    logger = configure_uvicorn_access_logger()
    initial_handlers = list(logger.handlers)
    configure_uvicorn_access_logger()

    assert logger.handlers == initial_handlers


def test_frontend_distinguishes_fit_diagnostic_from_ground_truth_benchmark():
    """TL-003-13/TL-003-23: 页面不得把同源拟合诊断冒充 Benchmark。"""
    frontend = (benchmark_localizers.REPO_ROOT / "web/app_v10.js").read_text(encoding="utf-8")

    assert "⚠ 低可信" in frontend
    assert "2D 几何拟合诊断（非 Benchmark）" in frontend
    assert "独立真值 Benchmark" in frontend
    assert "同源 NPY 不能作为米制验证" in frontend
    assert "SALAD+RoMa v2 (推荐)" not in frontend


def test_frontend_renders_query_and_final_projection_or_missing_reason():
    """TL-003-21: 视觉产物必须展示，缺失时不能静默空白。"""
    frontend = (benchmark_localizers.REPO_ROOT / "web/app_v10.js").read_text(encoding="utf-8")

    assert "result.query_image" in frontend
    assert "result.reprojection_image" in frontend
    assert "查询图像" in frontend
    assert "最终位姿投影" in frontend
    assert "视觉产物未生成" in frontend


def test_frontend_uses_migrated_local_query_image_point_crosscheck():
    """TL-003-27: 查询图本地选点并展示 H→SLAM 与 NPY 两套 XYZ。"""
    frontend = (benchmark_localizers.REPO_ROOT / "web/app_v10.js").read_text(encoding="utf-8")

    assert "verifyCoordinatePoint" in frontend
    assert "/localize/coordinate-transform" in frontend
    assert "task_id" in frontend
    assert "result_index" in frontend
    assert "H→SLAM XYZ" in frontend
    assert "NPY XYZ" in frontend
    assert "坐标交叉验证（非绝对精度）" in frontend
    assert "本地 H 内点" in frontend
    assert "SLAM_MAP_URL" not in frontend


def test_frontend_uses_coordinate_difference_as_primary_reliability_standard():
    """TL-003-30: 主结果必须展示坐标差判定，点数/相似度只能是辅助诊断。"""
    frontend = (benchmark_localizers.REPO_ROOT / "web/app_v10.js").read_text(encoding="utf-8")

    assert "坐标差最终判定" in frontend
    assert "中位坐标差" in frontend
    assert "判定门槛" in frontend
    assert "辅助几何诊断" in frontend


def test_projection_report_scripts_do_not_emit_same_source_meter_errors():
    """TL-003-24: 两条离线报告路径也必须移除同源 NPY 米制自比较。"""
    scripts = [
        benchmark_localizers.REPO_ROOT / "scripts/generate_verify_report.py",
        benchmark_localizers.REPO_ROOT / "scripts/verify_localization.py",
    ]

    for path in scripts:
        source = path.read_text(encoding="utf-8")
        assert "median_error_m" not in source
        assert "xyz_npy_via_H" not in source
        assert "median_residual_px" in source
