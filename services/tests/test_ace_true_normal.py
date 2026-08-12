from __future__ import annotations

import os
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

import services.localizer.coord_regression as coord_regression
import services.localizer.pose_utils as pose_utils
from services.localizer import enhanced_ace
import services.localizer.ace_localizer as ace_localizer
import services.localizer.normal_estimator as normal_estimator


@pytest.fixture
def fake_query_image(tmp_path):
    img = np.random.randint(0, 255, (64, 64, 3), dtype=np.uint8)
    path = tmp_path / "query_008.png"
    cv2.imwrite(str(path), img)
    return str(path)


def _make_fake_model() -> SimpleNamespace:
    return SimpleNamespace(encoder=SimpleNamespace(conv1=SimpleNamespace(in_channels=6)))


def _inject_pnp_success(monkeypatch):
    def fake_pnp(*args, **kwargs):
        n = 20
        return {
            "success": True,
            "rvec": np.zeros((3, 1), dtype=np.float64),
            "tvec": np.array([[0.0], [0.0], [5.0]], dtype=np.float64),
            "inliers": np.arange(n, dtype=int),
            "inlier_count": n,
            "reproj_error_px": 1.13,
            "score": 2.0,
        }

    monkeypatch.setattr(pose_utils, "solve_pnp_with_focal_search", fake_pnp)


def _mock_dsine_estimator(monkeypatch, value=0.25):
    monkeypatch.setattr(normal_estimator, "estimate_normal",
                        lambda img: np.full((*img.shape[:2], 3), value, dtype=np.float32))
    monkeypatch.setattr(normal_estimator, "normal_source_from_estimate",
                        lambda: normal_estimator.NORMAL_SOURCE_DSINE)


# ────────────────────────────────────────────────────────────────────
# TL-008-01 (AC-008-01)：estimate_normal 接口 + (n+1)*0.5 映射
# ────────────────────────────────────────────────────────────────────
def test_estimate_normal_shape_dtype_and_mapping_to_unit_interval(monkeypatch):
    """TL-008-01: 注入假 DSINE 输出 [-1,1] → estimate_normal 返回 (H,W,3) float32、值域 [0,1]（(n+1)*0.5 映射）。"""
    h, w = 32, 48
    image = np.random.randint(0, 255, (h, w, 3), dtype=np.uint8)
    fake_dsine = np.random.uniform(-1.0, 1.0, (h, w, 3)).astype(np.float32)

    monkeypatch.setattr(normal_estimator, "_raw_infer", lambda img: fake_dsine)

    result = normal_estimator.estimate_normal(image)

    assert result.shape == (h, w, 3), f"法线尺寸应与输入一致，实际 {result.shape}"
    assert result.dtype == np.float32, f"应为 float32，实际 {result.dtype}"
    expected = (fake_dsine + 1.0) * 0.5
    assert np.allclose(result, expected), "[-1,1] 真法线应按训练映射 (n+1)*0.5 转到 [0,1]"
    assert float(result.min()) >= 0.0 and float(result.max()) <= 1.0, "值域应为 [0,1]"
    assert normal_estimator.normal_source_from_estimate() == normal_estimator.NORMAL_SOURCE_DSINE


# ────────────────────────────────────────────────────────────────────
# TL-008-02 (AC-008-01)：权重缺失/加载失败 → 回退常量 0.5
# ────────────────────────────────────────────────────────────────────
def test_estimate_normal_weight_unavailable_falls_back_constant():
    """TL-008-02: 不注入任何模型（真实桩抛错）→ 优雅回退常量 0.5、normal_source=constant_fallback、不抛异常。"""
    h, w = 32, 48
    image = np.random.randint(0, 255, (h, w, 3), dtype=np.uint8)

    result = normal_estimator.estimate_normal(image)

    assert result.shape == (h, w, 3)
    assert result.dtype == np.float32
    assert np.allclose(result, 0.5), f"应回退常量 0.5，实际 min={result.min():.3f}, max={result.max():.3f}"
    assert float(result.min()) >= 0.0 and float(result.max()) <= 1.0
    assert normal_estimator.normal_source_from_estimate() == normal_estimator.NORMAL_SOURCE_FALLBACK


# ────────────────────────────────────────────────────────────────────
# TL-008-03 (AC-008-02)：ace_better/ace_normal 的 normal_mode 注入
# ────────────────────────────────────────────────────────────────────
def test_ace_better_normal_dsine_mode_passes_estimated_normal(monkeypatch, fake_query_image):
    """TL-008-03: ace_better 6ch + normal_mode="dsine"（mock 估计器返回 0.25）→ predict_dense 收到真法线（非常量/梯度），normal_source=dsine。"""
    captured = {}

    def fake_predict_dense(model, image, normal_map=None, **kwargs):
        captured["normal_map"] = normal_map
        n = 20
        return np.random.rand(n, 2) * 64, np.random.rand(n, 3) * 10 + 5, np.ones(n, dtype=np.float32)

    monkeypatch.setattr(os.path, "exists", lambda p: False)  # 强制 6ch 回退路由
    monkeypatch.setattr(coord_regression, "load_coord_regression", lambda *a, **k: _make_fake_model())
    monkeypatch.setattr(coord_regression, "predict_dense", fake_predict_dense)
    _inject_pnp_success(monkeypatch)
    monkeypatch.setattr("services.localizer._POINT_INDEX", {"tree": None})
    _mock_dsine_estimator(monkeypatch, value=0.25)

    result = enhanced_ace.ace_with_better_normal(fake_query_image, fov_deg=75, normal_mode="dsine")

    assert result["success"] is True
    nm = captured["normal_map"]
    assert nm is not None
    assert np.allclose(nm, 0.25), f"normal_mode=dsine 应收到 mock 真法线 0.25，实际 min={nm.min():.3f} max={nm.max():.3f}"
    assert not np.allclose(nm, 0.5), "不应回退为常量 0.5"
    assert result["normal_source"] == "dsine"


def test_ace_better_normal_constant_mode_keeps_007_behavior(monkeypatch, fake_query_image):
    """TL-008-03: ace_better 默认（normal_mode=constant）与 007 一致 — predict_dense 收到常量 0.5、normal_source=constant_fallback。"""
    captured = {}

    def fake_predict_dense(model, image, normal_map=None, **kwargs):
        captured["normal_map"] = normal_map
        n = 20
        return np.random.rand(n, 2) * 64, np.random.rand(n, 3) * 10 + 5, np.ones(n, dtype=np.float32)

    monkeypatch.setattr(os.path, "exists", lambda p: False)
    monkeypatch.setattr(coord_regression, "load_coord_regression", lambda *a, **k: _make_fake_model())
    monkeypatch.setattr(coord_regression, "predict_dense", fake_predict_dense)
    _inject_pnp_success(monkeypatch)
    monkeypatch.setattr("services.localizer._POINT_INDEX", {"tree": None})

    result = enhanced_ace.ace_with_better_normal(fake_query_image, fov_deg=75)

    assert result["success"] is True
    assert np.allclose(captured["normal_map"], 0.5), "constant 模式应保持常量 0.5 占位（007 行为）"
    assert result["input_mode"] == "ace_6ch_constant_normal"
    assert result["normal_source"] == "constant_fallback"


def test_ace_with_normal_dsine_mode_passes_estimated_normal(monkeypatch, fake_query_image):
    """TL-008-03: ace_normal（ace_localizer）6ch + normal_mode="dsine" → predict_dense 收到 mock 真法线 0.25。"""
    captured = {}

    def fake_predict_dense(model, image, normal_map=None, **kwargs):
        captured["normal_map"] = normal_map
        n = 20
        return np.random.rand(n, 2) * 64, np.random.rand(n, 3) * 10 + 5, np.ones(n, dtype=np.float32)

    monkeypatch.setattr(os.path, "exists", lambda p: False)
    monkeypatch.setattr(coord_regression, "load_coord_regression", lambda *a, **k: _make_fake_model())
    monkeypatch.setattr(coord_regression, "predict_dense", fake_predict_dense)
    _inject_pnp_success(monkeypatch)
    monkeypatch.setattr("services.localizer._POINT_INDEX", {"tree": None})
    _mock_dsine_estimator(monkeypatch, value=0.25)

    result = ace_localizer.ace_with_normal(fake_query_image, fov_deg=75, normal_mode="dsine")

    assert result["success"] is True
    nm = captured["normal_map"]
    assert nm is not None
    assert np.allclose(nm, 0.25), f"ace_normal dsine 应收到 mock 真法线 0.25，实际 min={nm.min():.3f} max={nm.max():.3f}"
    assert not np.allclose(nm, 0.5), "不应回退为常量 0.5"
    assert result["normal_source"] == "dsine"