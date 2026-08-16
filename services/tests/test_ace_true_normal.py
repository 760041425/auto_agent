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


def _reset_model_cache():
    """清除 normal_estimator 的懒加载缓存，避免测试间互相污染。"""
    normal_estimator._model = None
    normal_estimator._transform = None


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
    # P2 定案后端为 MiDaS（D-008-01）：_raw_infer 成功即标注 mi_das。
    assert normal_estimator.normal_source_from_estimate() == normal_estimator.NORMAL_SOURCE_MIDAS


# ────────────────────────────────────────────────────────────────────
# TL-008-02 (AC-008-01)：权重缺失/加载失败 → 回退常量 0.5
# ────────────────────────────────────────────────────────────────────
def test_estimate_normal_weight_unavailable_falls_back_constant(monkeypatch):
    """TL-008-02: 模拟真实模型加载失败（monkeypatch _load_model 抛 NormalModelNotReadyError）
    → 优雅回退常量 0.5、normal_source=constant_fallback、不抛异常。"""
    _reset_model_cache()

    def _boom():
        raise normal_estimator.NormalModelNotReadyError("模拟：权重不可达")

    monkeypatch.setattr(normal_estimator, "_load_model", _boom)

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


# ────────────────────────────────────────────────────────────────────
# TL-008-04 (AC-008-01)：真实 MiDaS 端到端可达性（需外网/缓存，默认跳过）
# ────────────────────────────────────────────────────────────────────
@pytest.mark.integration
def test_estimate_normal_real_midas_end_to_end():
    """TL-008-04: 不注入、不走 monkeypatch — 真实加载 MiDaS_small 并对随机图推理。

    验收：
    - 返回 (H,W,3) float32、值域 [0,1]；
    - 法线经 depth_to_normals 归一化（|n| 均值 ≈ 1.0）；
    - 分布与训练真法线对齐（[0,1] mean ≈ 0.4-0.6，训练 mean 0.5）；
    - normal_source == mi_das（非 fallback）。

    首次运行会触发 torch.hub 下载（~170s，缓存到 TORCH_HOME）；后续 ~5s。
    需要外网或已缓存权重；无缓存且离线时跳过。
    """
    _reset_model_cache()
    h, w = 64, 64
    image = np.random.randint(0, 255, (h, w, 3), dtype=np.uint8)

    result = normal_estimator.estimate_normal(image)

    assert result.shape == (h, w, 3), f"尺寸应为 {(h, w, 3)}，实际 {result.shape}"
    assert result.dtype == np.float32
    assert 0.0 <= float(result.min()) and float(result.max()) <= 1.0, "值域应落入 [0,1]"

    # 反推 [-1,1] 法线，检查归一化（|n| ≈ 1）。
    n_xyz = result * 2.0 - 1.0
    mag = np.linalg.norm(n_xyz, axis=-1)
    assert float(mag.mean()) > 0.8, f"法线应近似归一化，|n| 均值={mag.mean():.3f}"

    # 分布对齐训练真法线（[0,1] mean ≈ 0.5，容差宽松因随机图）。
    assert 0.3 <= float(result.mean()) <= 0.7, f"[0,1] mean={result.mean():.3f}，应接近训练 0.5"

    assert normal_estimator.normal_source_from_estimate() == normal_estimator.NORMAL_SOURCE_MIDAS, \
        "真实 MiDaS 加载后 source 应为 mi_das"


# ────────────────────────────────────────────────────────────────────
# TL-008-07 (AC-008-01)：非法输入显式报错，不被降级静默吞掉
# ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "bad_image,expected_hint",
    [
        # 非 uint8：float32 (H,W,3) —— 应报 dtype 错误
        (lambda: np.random.rand(32, 48, 3).astype(np.float32), "uint8"),
        # 非 uint8：float64 (H,W,3)
        (lambda: np.random.rand(32, 48, 3).astype(np.float64), "uint8"),
        # 非 3 通道：uint8 (H,W,4)
        (lambda: np.random.randint(0, 255, (32, 48, 4), dtype=np.uint8), "3"),
        # 非 3 通道：uint8 (H,W,1)
        (lambda: np.random.randint(0, 255, (32, 48, 1), dtype=np.uint8), "3"),
        # 非 3 维：uint8 (H,W) —— 应报通道/维度错误
        (lambda: np.random.randint(0, 255, (32, 48), dtype=np.uint8), "3"),
    ],
    ids=["float32", "float64", "4ch", "1ch", "2d"],
)
def test_estimate_normal_rejects_invalid_input(monkeypatch, bad_image, expected_hint):
    """TL-008-07: estimate_normal 对非法输入（非 uint8 / 非 3 通道 / 非 3 维）应显式 ValueError。

    关键：非法输入是编程错误，不应被末端 `except Exception` 静默降级为常量 0.5。
    因此校验必须在 try 之前，且错误消息应包含具体 dtype/通道数以便诊断。
    """
    _reset_model_cache()
    bad = bad_image()
    # 隔离模型依赖：只要校验在 try 之前，根本不会走到 _raw_infer。
    monkeypatch.setattr(normal_estimator, "_raw_infer",
                        lambda img: np.zeros((*img.shape[:2], 3), dtype=np.float32))

    with pytest.raises(ValueError, match=expected_hint):
        normal_estimator.estimate_normal(bad)