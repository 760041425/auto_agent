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


@pytest.fixture
def fake_query_image(tmp_path):
    img = np.random.randint(0, 255, (64, 64, 3), dtype=np.uint8)
    path = tmp_path / "query_007.png"
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


# ────────────────────────────────────────────────────────────────────
# TL-007-01 (AC-007-01)：6ch 回退路径传入常量 0.5 法线占位，非梯度噪声
# ────────────────────────────────────────────────────────────────────
def test_ace_better_normal_6ch_fallback_passes_constant_normal(monkeypatch, fake_query_image):
    """TL-007-01: ace_better 6ch 回退时 predict_dense 收到常量 0.5 法线，并标注 input_mode/normal_source。"""
    captured = {}

    def fake_predict_dense(model, image, normal_map=None, **kwargs):
        captured["normal_map"] = normal_map
        n = 20
        pts_2d = np.random.rand(n, 2) * 64
        pts_3d = np.random.rand(n, 3) * 10 + 5
        confidence = np.ones(n, dtype=np.float32)
        return pts_2d, pts_3d, confidence

    monkeypatch.setattr(coord_regression, "predict_dense", fake_predict_dense)
    monkeypatch.setattr(coord_regression, "load_coord_regression", lambda *a, **k: _make_fake_model())
    monkeypatch.setattr(os.path, "exists", lambda p: False)
    _inject_pnp_success(monkeypatch)

    # 避免加载 LAS 点云（测试环境无真实 COLMAP 缓存）
    monkeypatch.setattr("services.localizer._POINT_INDEX", {"tree": None})

    result = enhanced_ace.ace_with_better_normal(fake_query_image, fov_deg=75)

    assert result["success"] is True
    nm = captured["normal_map"]
    assert nm is not None
    assert np.allclose(nm, 0.5), f"回退路径法线应为常量 0.5 占位，实际收到梯度噪声（min={nm.min():.3f}, max={nm.max():.3f}）"
    assert result["input_mode"] == "ace_6ch_constant_normal"
    assert result["normal_source"] == "constant_fallback"


# ────────────────────────────────────────────────────────────────────
# TL-007-02 (AC-007-02)：模型路由 — scene 3ch 优先 / 6ch 回退
# ────────────────────────────────────────────────────────────────────
def test_resolve_ace_model_route_prefers_scene3ch(monkeypatch, fake_query_image):
    """TL-007-02a: scene 3ch 模型存在时走 RGB-only，predict_dense 不传 normal_map。"""
    captured = {}

    def fake_predict_dense(model, image, normal_map=None, **kwargs):
        captured["normal_map"] = normal_map
        n = 20
        return np.random.rand(n, 2) * 64, np.random.rand(n, 3) * 10 + 5, np.ones(n, dtype=np.float32)

    monkeypatch.setattr(os.path, "exists", lambda p: True)  # 模拟 scene 模型存在
    monkeypatch.setattr(enhanced_ace.torch, "load", lambda *a, **k: {"dummy_state": 1})
    monkeypatch.setattr(coord_regression, "_detect_architecture", lambda sd: {"in_channels": 3})
    monkeypatch.setattr(coord_regression, "load_coord_regression", lambda *a, **k: _make_fake_model())
    monkeypatch.setattr(coord_regression, "predict_dense", fake_predict_dense)
    _inject_pnp_success(monkeypatch)
    monkeypatch.setattr("services.localizer._POINT_INDEX", {"tree": None})

    model, info = enhanced_ace.resolve_ace_model()
    assert info["model_path"] == enhanced_ace.SCENE_MODEL_PATH
    assert info["in_channels"] == 3
    assert info["input_mode"] == "ace_scene_rgb3ch"

    result = enhanced_ace.ace_with_better_normal(fake_query_image, fov_deg=75)
    assert result["success"] is True
    assert captured["normal_map"] is None, "3ch 路径不应向 predict_dense 传 normal_map"
    assert result["input_mode"] == "ace_scene_rgb3ch"
    assert result["normal_source"] == "none_rgb3ch"


def test_resolve_ace_model_route_fallback_6ch_constant(monkeypatch):
    """TL-007-02b: scene 模型不存在时回退默认 6ch + 常量占位。"""
    fake_model = _make_fake_model()
    monkeypatch.setattr(os.path, "exists", lambda p: False)
    monkeypatch.setattr(coord_regression, "load_coord_regression", lambda *a, **k: fake_model)

    model, info = enhanced_ace.resolve_ace_model()

    assert model is fake_model
    assert info["model_path"] == enhanced_ace.DEFAULT_MODEL_PATH
    assert info["in_channels"] == 6
    assert info["input_mode"] == "ace_6ch_constant_normal"
    assert info["normal_source"] == "constant_fallback"


# ────────────────────────────────────────────────────────────────────
# TL-007-03 (AC-007-03)：solve_pnp_with_focal_search 返回 attempts_summary
# ────────────────────────────────────────────────────────────────────
def test_solve_pnp_focal_search_failure_returns_attempts_summary():
    """TL-007-03a: 无内点失败分支也带出 attempts_summary（候选数/best 统计）。"""
    img_w, img_h = 640, 480
    rng = np.random.RandomState(7)
    pts_2d = rng.rand(200, 2) * np.array([img_w, img_h])
    pts_3d = rng.rand(200, 3) * 100.0

    res = pose_utils.solve_pnp_with_focal_search(
        pts_3d, pts_2d, img_w, img_h,
        reproj_error=8.0, min_inliers=6, fov_deg=75.0, ransac_seed=1337,
    )

    assert res["success"] is False
    summ = res["attempts_summary"]
    assert summ["tried_candidates"] >= 1
    assert summ["best_inliers"] >= 0
    assert np.isfinite(summ["best_reproj_error_px"]), "best_reproj_error_px 应为有限数值"
    assert summ["best_reproj_error_px"] >= 0


def test_solve_pnp_focal_search_success_returns_attempts_summary():
    """TL-007-03b: 成功分支同样带出 attempts_summary。"""
    img_w, img_h = 640, 480
    fov_deg = 75.0
    f = max(img_w, img_h) / (2 * np.tan(np.deg2rad(fov_deg / 2)))
    K = np.array([[f, 0, img_w / 2], [0, f, img_h / 2], [0, 0, 1]], dtype=np.float64)
    rvec_true, _ = cv2.Rodrigues(np.array([0.05, -0.03, 0.02]))
    tvec_true = np.array([[0.5], [0.2], [8.0]], dtype=np.float64)

    rng = np.random.RandomState(1)
    obj = rng.uniform(-3, 3, size=(100, 3)).astype(np.float64)
    obj[:, 2] += 8.0
    img_pts, _ = cv2.projectPoints(obj, rvec_true, tvec_true, K, None)
    img_pts = img_pts.reshape(-1, 2)

    res = pose_utils.solve_pnp_with_focal_search(
        obj, img_pts, img_w, img_h,
        fov_deg=fov_deg, reproj_error=8.0, min_inliers=6, ransac_seed=1337,
    )

    assert res["success"] is True
    summ = res["attempts_summary"]
    assert summ["tried_candidates"] >= 1
    assert summ["best_inliers"] >= 6
    assert np.isfinite(summ["best_reproj_error_px"])


# ────────────────────────────────────────────────────────────────────
# TL-007-04 (AC-007-04)：ACE 系 PnP 失败返回 diagnostics 结构
# ────────────────────────────────────────────────────────────────────
def test_ace_better_normal_pnp_failure_includes_diagnostics(monkeypatch, fake_query_image):
    """TL-007-04: mock PnP 失败 → 结果含 diagnostics.{pnp, pred_xyz, las_bbox, overlap, model, input_mode}。"""

    def fake_predict_dense(model, image, normal_map=None, **kwargs):
        n = 30
        return np.random.rand(n, 2) * 64, np.random.rand(n, 3) * 10 + 5, np.ones(n, dtype=np.float32)

    def fake_pnp_fail(*args, **kwargs):
        return {
            "success": False,
            "error": "所有焦距候选 PnP 失败（尝试 15 次）",
            "attempts_summary": {"tried_candidates": 15, "best_inliers": 3, "best_reproj_error_px": 41.2},
        }

    monkeypatch.setattr(os.path, "exists", lambda p: False)
    monkeypatch.setattr(coord_regression, "load_coord_regression", lambda *a, **k: _make_fake_model())
    monkeypatch.setattr(coord_regression, "predict_dense", fake_predict_dense)
    monkeypatch.setattr(pose_utils, "solve_pnp_with_focal_search", fake_pnp_fail)
    las_pts = np.array([[0.0, 0.0, 0.0], [10.0, 10.0, 10.0]], dtype=np.float64)
    monkeypatch.setattr("services.localizer._POINT_INDEX", {"tree": None, "pts": las_pts})

    result = enhanced_ace.ace_with_better_normal(fake_query_image, fov_deg=75)

    assert result["success"] is False
    assert result["error"] == "ACE PnP 失败"
    diag = result["diagnostics"]
    assert diag["pnp"]["tried_candidates"] == 15
    assert diag["pnp"]["best_inliers"] == 3
    assert np.isfinite(diag["pnp"]["best_reproj_error_px"])
    assert {"z_min", "z_max", "center", "count"} <= set(diag["pred_xyz"])
    assert diag["pred_xyz"]["count"] == 30
    assert diag["las_bbox"]["z_min"] == 0.0
    assert 0.0 <= diag["overlap_with_las_bbox"] <= 1.0
    assert diag["model"]["path"] == enhanced_ace.DEFAULT_MODEL_PATH
    assert diag["model"]["in_channels"] == 6
    assert diag["input_mode"] == "ace_6ch_constant_normal"


# ────────────────────────────────────────────────────────────────────
# TL-007-05 (AC-007-05)：低点分支优雅失败，不再 NameError
# ────────────────────────────────────────────────────────────────────
def test_ace_with_normal_low_point_branch_no_nameerror(monkeypatch, fake_query_image):
    """TL-007-05: predict_dense 置信度过滤后点数不足 → 优雅失败（result 死代码移除）。"""

    def fake_predict_dense(model, image, normal_map=None, **kwargs):
        n = 10
        pts_2d = np.random.rand(n, 2) * 64
        pts_3d = np.random.rand(n, 3) * 10 + 5
        confidence = np.zeros(n, dtype=np.float32)  # 全部低置信 → mask 后点数不足
        return pts_2d, pts_3d, confidence

    monkeypatch.setattr(os.path, "exists", lambda p: False)
    monkeypatch.setattr(coord_regression, "load_coord_regression", lambda *a, **k: _make_fake_model())
    monkeypatch.setattr(coord_regression, "predict_dense", fake_predict_dense)
    monkeypatch.setattr("services.localizer._POINT_INDEX", {"tree": None})

    result = ace_localizer.ace_with_normal(fake_query_image, fov_deg=75)

    assert result["success"] is False
    assert result["error"] == "ACE 预测点不足"
    assert result["tag"] == "ace_normal"