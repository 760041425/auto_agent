import cv2
import numpy as np
import torch
import pytest

from services.localizer import _render_point_cloud_splat, _estimate_surface_normals
from services.localizer.salad_roma import (
    _apply_coordinate_reliability,
    _is_pose_better,
    _select_compatible_salad_cache,
)


def test_pose_selector_prefers_higher_inlier_count_and_lower_error():
    """TL-002-06: 位姿优选以内点数优先、误差次优。"""
    assert _is_pose_better(12, 3.5, 8, 4.2)
    assert _is_pose_better(8, 2.0, 8, 2.2)
    assert not _is_pose_better(8, 3.0, 10, 2.2)


def test_original_salad_rejects_stale_cache_and_reuses_v2_multi_descriptors(tmp_path):
    """TL-003-31: 旧缓存与当前 tile 零交集时必须选用兼容的 V2 multi 缓存。"""
    stale_path = tmp_path / "salad_index.npz"
    v2_path = tmp_path / "salad_index_v2.npz"
    current_key = "view_yaw45_24.9_2.0_0.5_13457_p+0"
    stale_desc = np.ones(1152, dtype=np.float32)
    compatible_desc = np.full(1152, 2.0, dtype=np.float32)

    np.savez_compressed(
        stale_path,
        keys=np.array(["view_yaw45_24.9_2.0_0.5_13457"], dtype=object),
        descs=np.array([stale_desc], dtype=np.float32),
    )
    np.savez(
        v2_path,
        **{
            current_key: np.array(
                {"rgb": np.ones(384, dtype=np.float32), "multi": compatible_desc},
                dtype=object,
            )
        },
    )

    selected, source = _select_compatible_salad_cache(
        {current_key}, stale_path, v2_path, expected_dim=1152
    )

    assert source == "v2_multi"
    assert set(selected) == {current_key}
    assert np.array_equal(selected[current_key], compatible_desc)


def test_roma_match_uses_tiny_roma_instead_of_lightglue(monkeypatch):
    """TL-003-32: SALAD+RoMa 标签必须执行 TinyRoMa 匹配器。"""
    from services.localizer import salad_roma

    class FakeRoMa:
        def match(self, image0, image1, *, batched):
            assert batched is False
            assert image0.shape == (1, 3, 8, 12)
            assert image1.shape == (1, 3, 6, 10)
            return torch.zeros((2, 2, 4)), torch.ones((2, 2))

        def sample(self, matches, certainty, num):
            assert num == 2
            return (
                torch.tensor([[-1.0, -1.0, 1.0, 1.0], [0.0, 0.0, 0.0, 0.0]]),
                torch.tensor([0.9, 0.8]),
            )

        def to_pixel_coordinates(self, matches, h0, w0, h1, w1):
            assert (h0, w0, h1, w1) == (8, 12, 6, 10)
            return torch.tensor([[0.0, 0.0], [6.0, 4.0]]), torch.tensor([[9.0, 5.0], [5.0, 3.0]])

    monkeypatch.setattr(salad_roma, "_get_roma_model", lambda: FakeRoMa())
    monkeypatch.setattr(
        salad_roma,
        "_lightglue_match",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("LightGlue must not run")),
    )

    kpts0, kpts1, certainty = salad_roma._roma_match(
        np.zeros((8, 12, 3), dtype=np.uint8),
        np.zeros((6, 10, 3), dtype=np.uint8),
        sample_num=2,
    )

    assert kpts0.tolist() == [[0.0, 0.0], [6.0, 4.0]]
    assert kpts1.tolist() == [[9.0, 5.0], [5.0, 3.0]]
    assert certainty.tolist() == pytest.approx([0.9, 0.8])


def test_original_salad_high_inliers_cannot_override_coordinate_failure():
    """TL-003-33: 原版同样只以严格 0.3m 坐标差决定可信状态。"""
    result = {"success": True, "inliers": 607, "reliable": True}
    context = {
        "status": "ready",
        "consistency": {
            "status": "available",
            "threshold_m": 0.3,
            "median_m": 2.5,
            "passed": False,
        },
    }

    updated = _apply_coordinate_reliability(result, context)

    assert updated["success"] is True
    assert updated["inliers"] == 607
    assert updated["coordinate_transform"] == context
    assert updated["reliable"] is False


def test_original_salad_retrieval_excludes_rejected_map_tiles(monkeypatch):
    """TL-002-13: 原版 SALAD 同样只能在 accepted MapTile 发布集合中检索。"""
    from services.localizer import salad_roma

    accepted_key = "view_yaw0_1.0_2.0_0.5_8_p-15"
    rejected_key = "view_yaw0_1.0_2.0_0.5_0_p+0"
    monkeypatch.setattr(salad_roma, "_SALAD_INDEX", {
        accepted_key: np.array([0.8, 0.2], dtype=np.float32),
        rejected_key: np.array([1.0, 0.0], dtype=np.float32),
    })
    monkeypatch.setattr(salad_roma, "_get_dinov2_model", lambda *args, **kwargs: (object(), 1.0))
    monkeypatch.setattr(
        salad_roma,
        "_extract_multimodal_descriptor",
        lambda *args, **kwargs: np.array([1.0, 0.0], dtype=np.float32),
    )
    monkeypatch.setattr(salad_roma, "_load_tile_index", lambda: [
        {"image_path": f"projections/tiles/{rejected_key}.png", "accepted": False},
        {"image_path": f"projections/tiles/{accepted_key}.png", "accepted": True},
    ])

    results = salad_roma._salad_retrieve(np.zeros((8, 8, 3), dtype=np.uint8), top_k=2)

    assert [key for key, _, _ in results] == [accepted_key]


def test_v2_retrieval_never_returns_stale_keys_outside_current_tile_publication(monkeypatch):
    """TL-002-13: 描述子旧 key 不得绕过当前 accepted MapTile 发布清单。"""
    from services.localizer import salad_roma_v2

    valid_key = "view_yaw0_1.0_2.0_0.5_8_p-15"
    stale_key = "view_yaw0_1.0_2.0_0.5_0_p-30"
    monkeypatch.setattr(salad_roma_v2, "_SALAD_INDEX_V2", {
        stale_key: {"rgb": np.array([1.0, 0.0], dtype=np.float32)},
        valid_key: {"rgb": np.array([0.8, 0.2], dtype=np.float32)},
    })
    monkeypatch.setattr(salad_roma_v2, "_get_dinov2_model", lambda: (object(), 1.0))
    monkeypatch.setattr(
        salad_roma_v2,
        "_extract_rgb_descriptor",
        lambda model, image, scale: np.array([1.0, 0.0], dtype=np.float32),
    )
    monkeypatch.setattr(salad_roma_v2, "_load_tile_index", lambda: [{
        "image_path": f"projections/tiles/{valid_key}.png",
        "npy_path": f"projections/tiles/{valid_key}.npy",
        "normal_path": f"projections/tiles/{valid_key}_normal.npy",
        "view": "yaw0",
        "tile": "1.0_2.0_0.5",
        "pitch_deg": -15.0,
        "accepted": True,
    }])

    results = salad_roma_v2._salad_retrieve_v2(
        np.zeros((8, 8, 3), dtype=np.uint8),
        top_k=3,
    )

    assert [key for key, _, _ in results] == [valid_key]
    assert results[0][2]["pitch_deg"] == -15.0


def test_v2_retrieval_excludes_leave_one_out_query_without_mutating_shared_index(monkeypatch):
    """TL-010-08：真实检索必须排除查询 tile，且不得污染进程共享缓存。"""
    from services.localizer import salad_roma_v2

    query_key = "view_yaw0_1.0_2.0_0.5_8_p-15"
    neighbor_key = "view_yaw0_2.0_2.0_0.5_9_p-15"
    monkeypatch.setattr(
        salad_roma_v2,
        "_SALAD_INDEX_V2",
        {
            query_key: {"rgb": np.array([1.0, 0.0], dtype=np.float32)},
            neighbor_key: {"rgb": np.array([0.8, 0.2], dtype=np.float32)},
        },
    )
    monkeypatch.setattr(salad_roma_v2, "_get_dinov2_model", lambda: (object(), 1.0))
    monkeypatch.setattr(
        salad_roma_v2,
        "_extract_rgb_descriptor",
        lambda model, image, scale: np.array([1.0, 0.0], dtype=np.float32),
    )
    monkeypatch.setattr(
        salad_roma_v2,
        "_load_tile_index",
        lambda: [
            {
                "image_path": f"projections/tiles/{query_key}.png",
                "view": "yaw0",
                "tile": "1.0_2.0_0.5",
                "accepted": True,
            },
            {
                "image_path": f"projections/tiles/{neighbor_key}.png",
                "view": "yaw0",
                "tile": "2.0_2.0_0.5",
                "accepted": True,
            },
        ],
    )

    results = salad_roma_v2._salad_retrieve_v2(
        np.zeros((8, 8, 3), dtype=np.uint8),
        top_k=1,
        excluded_tile_keys={query_key},
    )

    assert [key for key, _, _ in results] == [neighbor_key]
    assert query_key in salad_roma_v2._SALAD_INDEX_V2


def test_render_point_cloud_splat_produces_soft_image():
    points = np.array([[0.0, 0.0, 2.0], [0.2, 0.0, 3.0]], dtype=np.float64)
    colors = np.array([[255, 0, 0], [0, 255, 0]], dtype=np.uint8)
    rvec = np.array([0.0, 0.0, 0.0], dtype=np.float64)
    tvec = np.array([0.0, 0.0, 4.0], dtype=np.float64)
    camera = np.array([[64.0, 0.0, 32.0], [0.0, 64.0, 32.0], [0.0, 0.0, 1.0]], dtype=np.float64)

    img = _render_point_cloud_splat(points, colors, camera, 64, 64, rvec=rvec, tvec=tvec, radius=1.5)

    assert img.shape == (64, 64, 3)
    assert np.count_nonzero(img) > 0
    assert img.dtype == np.uint8


def test_estimate_surface_normals_returns_valid_vectors():
    """TL-002-07: 表面法线输出稳定形状和单位向量。"""
    points = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float64)
    normals = _estimate_surface_normals(points)

    assert normals.shape == (3, 3)
    assert np.allclose(np.linalg.norm(normals, axis=1), 1.0, atol=1e-6)


# --------------------------------------------------------------------------- #
# v2 新增测试
# --------------------------------------------------------------------------- #


def test_quaternion_stability_180_degree():
    """v2: 180° 旋转时四元数不应 NaN/Inf。"""
    from services.localizer.pose_utils import rotation_matrix_to_quaternion
    R = np.diag([1.0, -1.0, -1.0])  # 绕 X 轴 180°
    q = rotation_matrix_to_quaternion(R)
    assert np.isfinite(q).all()
    assert abs(np.linalg.norm(q) - 1.0) < 1e-6


def test_quaternion_roundtrip_identity():
    """v2: 四元数 ↔ 旋转矩阵往返一致。"""
    from services.localizer.pose_utils import (
        rotation_matrix_to_quaternion, quaternion_to_rotation_matrix,
    )
    R_orig = cv2.Rodrigues(np.array([0.3, -0.2, 0.5]))[0]
    q = rotation_matrix_to_quaternion(R_orig)
    R_back = quaternion_to_rotation_matrix(q)
    assert np.allclose(R_orig, R_back, atol=1e-6)


def test_build_3d_2d_matches_filters_nan_and_zero():
    """v2: NPY 中 NaN 和 [0,0,0] 哨兵都被过滤。"""
    from services.localizer.salad_roma_v2 import _build_3d_2d_matches_v2
    coord_map = np.zeros((10, 10, 3), dtype=np.float64)
    coord_map[5, 5] = [1.0, 2.0, 3.0]      # 有效
    coord_map[3, 3] = [np.nan, 0, 0]        # NaN
    coord_map[2, 2] = [0.0, 0.0, 0.0]      # 零哨兵
    kpts_q = np.array([[5, 5], [3, 3], [2, 2]], dtype=np.float64)
    kpts_t = np.array([[5, 5], [3, 3], [2, 2]], dtype=np.float64)
    cert = np.array([0.9, 0.9, 0.9])
    obj, img = _build_3d_2d_matches_v2(kpts_q, kpts_t, cert, coord_map, min_cert=0.1)
    assert len(obj) == 1
    assert np.allclose(obj[0], [1.0, 2.0, 3.0])


def test_build_3d_2d_matches_filters_low_confidence():
    """v2: min_cert 阈值生效。"""
    from services.localizer.salad_roma_v2 import _build_3d_2d_matches_v2
    coord_map = np.zeros((10, 10, 3), dtype=np.float64)
    coord_map[5, 5] = [1.0, 2.0, 3.0]
    kpts_q = np.array([[5, 5]], dtype=np.float64)
    kpts_t = np.array([[5, 5]], dtype=np.float64)
    cert_low = np.array([0.05])
    obj, _ = _build_3d_2d_matches_v2(kpts_q, kpts_t, cert_low, coord_map, min_cert=0.1)
    assert len(obj) == 0


def test_essential_matrix_verification_rejects_outliers():
    """v2: E-matrix RANSAC 能剔除误匹配。"""
    from services.localizer.pose_utils import verify_essential_matrix
    n = 30
    pts1 = np.random.rand(n, 2).astype(np.float64) * 500 + 50
    # 内点：加小噪声
    pts2 = pts1 + np.random.randn(n, 2).astype(np.float64) * 0.5
    # 外点：随机跳变
    pts2[25:] += np.random.randn(5, 2).astype(np.float64) * 100
    K = np.array([[500, 0, 256], [0, 500, 256], [0, 0, 1]], dtype=np.float64)
    mask, n_ok = verify_essential_matrix(pts1, pts2, K, threshold=1.0)
    assert mask is not None
    assert n_ok >= n - 10  # 大部分内点保留


def test_solve_pnp_with_refine():
    """v2: PnP + refine 返回有效位姿。"""
    from services.localizer.pose_utils import solve_pnp_ransac
    # 构造已知位姿的 3D-2D 对应
    R_true = cv2.Rodrigues(np.array([0.1, 0.2, 0.05]))[0]
    t_true = np.array([[0], [0], [5]], dtype=np.float64)
    pts3d = np.random.randn(20, 3).astype(np.float64)
    K = np.array([[500, 0, 256], [0, 500, 256], [0, 0, 1]], dtype=np.float64)
    proj, _ = cv2.projectPoints(pts3d, cv2.Rodrigues(R_true)[0], t_true, K, None)
    pts2d = proj.reshape(-1, 2) + np.random.randn(20, 2) * 0.2
    rvec, tvec, inliers = solve_pnp_ransac(pts3d, pts2d, K,
                                           reproj_error=4.0, refine=True)
    assert rvec is not None
    assert inliers is not None
    assert len(inliers) >= 15


def test_resize_keep_aspect_ratio():
    """v2: 保持宽高比 resize 不拉伸。"""
    from services.localizer.pose_utils import resize_keep_aspect, map_coords_to_original
    img = np.zeros((400, 800, 3), dtype=np.uint8)  # 1:2
    out, scale, pad = resize_keep_aspect(img, target_size=512)
    assert out.shape == (512, 512, 3)
    # 内容应在 256 高、512 宽
    assert scale == pytest.approx(512 / 800)
    assert pad[0] == 0
    assert pad[1] > 0
    # 坐标映射回原图
    pts_out = np.array([[256.0, 256.0]])
    pts_orig = map_coords_to_original(pts_out, scale, pad)
    assert pts_orig[0, 0] == pytest.approx(800 * 0.5, abs=1.0)


def test_adaptive_early_stop():
    """v2: 收敛时早停。"""
    from services.localizer.pose_utils import adaptive_early_stop
    # 持续下降 → 不停
    assert not adaptive_early_stop([5.0, 4.0, 3.0, 2.0, 1.0], patience=1)
    # 收敛 → 停
    assert adaptive_early_stop([5.0, 1.0, 1.001, 1.0005], patience=1)


def test_coord_regression_loads_cpu():
    """v2: CoordRegressionFCN 可实例化并在 CPU 前向。"""
    from services.localizer.coord_regression import CoordRegressionFCN
    model = CoordRegressionFCN(in_channels=6, num_encoder_features=64)
    x = torch.randn(1, 6, 64, 64)
    with torch.no_grad():
        out = model(x)
    assert out.shape == (1, 3, 8, 8)  # 1/8 subsampled


def test_coord_regression_alias():
    """v2: CoordRegression 是 CoordRegressionFCN 的别名。"""
    from services.localizer.coord_regression import CoordRegression, CoordRegressionFCN
    assert CoordRegression is CoordRegressionFCN


def test_is_pose_better_v2_compatible():
    """v2: pose_utils.is_pose_better 与原版行为一致。"""
    from services.localizer.pose_utils import is_pose_better
    assert is_pose_better(12, 3.5, 8, 4.2)
    assert is_pose_better(8, 2.0, 8, 2.2)
    assert not is_pose_better(8, 3.0, 10, 2.2)


# --------------------------------------------------------------------------- #
#  BUG-003-05: 精化步骤复用初始匹配器
# --------------------------------------------------------------------------- #


def _mock_render(monkeypatch, tmp_path):
    """Helper: mock render_projection_image to write a real PNG and return valid coord map."""
    from services.localizer import salad_roma

    def _render(*a, **kw):
        out_path = tmp_path / "_refine_roma_refine_proj.png"
        cv2.imwrite(str(out_path), np.zeros((8, 8, 3), dtype=np.uint8))
        # coord_map 需要非零且有限，否则 _build_3d_2d_matches 会过滤掉
        coord = np.zeros((8, 8, 3), dtype=np.float64)
        for y in range(8):
            for x in range(8):
                coord[y, x] = [float(x), float(y), 1.0]
        return str(out_path), coord
    monkeypatch.setattr(salad_roma, "render_projection_image", _render)
    monkeypatch.setattr(salad_roma, "_get_dinov2_model", lambda: (None, 1.0))
    monkeypatch.setattr(
        salad_roma, "_solve_pnp",
        lambda *a, **kw: (np.zeros((3, 1)), np.zeros((3, 1)), np.array([0, 1])),
    )


def test_refine_uses_tiny_roma_when_matcher_type_is_roma(monkeypatch, tmp_path):
    """TL-003-34: matcher_type='tiny_roma' 必须分派到 TinyRoMa 而非 LightGlue。"""
    from services.localizer import salad_roma

    calls = []

    class FakeRoMa:
        def match(self, image0, image1, *, batched):
            calls.append("roma_match")
            return torch.zeros((2, 2, 4)), torch.ones((2, 2))

        def sample(self, matches, certainty, num):
            # 返回 12 个采样点（≥10 阈值）
            kpts = torch.tensor([[float(i), float(i), float(i + 1), float(i + 1)] for i in range(12)])
            return kpts, torch.ones(12) * 0.9

        def to_pixel_coordinates(self, matches, h0, w0, h1, w1):
            kpts0 = torch.tensor([[float(i), float(i)] for i in range(12)])
            kpts1 = torch.tensor([[float(i + 1), float(i + 1)] for i in range(12)])
            return kpts0, kpts1

    monkeypatch.setattr(salad_roma, "_get_roma_model", lambda: FakeRoMa())
    monkeypatch.setattr(
        salad_roma,
        "_lightglue_match",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("LightGlue must not run for tiny_roma")),
    )
    _mock_render(monkeypatch, tmp_path)

    out = tmp_path / "refine_out"
    out.mkdir()
    dummy_query = tmp_path / "query.png"
    cv2.imwrite(str(dummy_query), np.zeros((8, 8, 3), dtype=np.uint8))

    result = salad_roma.refine_pose_with_roma(
        str(dummy_query),
        np.zeros((3, 1)), np.zeros((3, 1)),
        np.eye(3), 8, 8,
        np.zeros((1, 3)), np.zeros((1, 3)),
        out_dir=str(out),
        matcher_type="tiny_roma",
    )

    assert result["success"] is True
    assert "roma_match" in calls


def test_refine_defaults_to_lightglue_for_backward_compat(monkeypatch, tmp_path):
    """TL-003-34: 无 matcher_type 参数时默认 LightGlue，保持向后兼容。"""
    from services.localizer import salad_roma

    calls = []

    def _fake_lg(*a, **kwargs):
        calls.append("lightglue")
        return (
            np.array([[float(i), float(i)] for i in range(12)]),
            np.array([[float(i + 1), float(i + 1)] for i in range(12)]),
            np.ones(12) * 0.9,
        )

    monkeypatch.setattr(salad_roma, "_lightglue_match", _fake_lg)
    _mock_render(monkeypatch, tmp_path)

    out = tmp_path / "refine_out"
    out.mkdir()
    dummy_query = tmp_path / "query.png"
    cv2.imwrite(str(dummy_query), np.zeros((8, 8, 3), dtype=np.uint8))

    result = salad_roma.refine_pose_with_roma(
        str(dummy_query),
        np.zeros((3, 1)), np.zeros((3, 1)),
        np.eye(3), 8, 8,
        np.zeros((1, 3)), np.zeros((1, 3)),
        out_dir=str(out),
        # 不传 matcher_type → 默认 LightGlue
    )

    assert result["success"] is True
    assert "lightglue" in calls


def test_refine_rejects_unknown_matcher_type(monkeypatch, tmp_path):
    """TL-003-34: 未知 matcher_type 返回结构化错误。"""
    from services.localizer import salad_roma

    _mock_render(monkeypatch, tmp_path)

    out = tmp_path / "refine_out"
    out.mkdir()
    dummy_query = tmp_path / "query.png"
    cv2.imwrite(str(dummy_query), np.zeros((8, 8, 3), dtype=np.uint8))

    result = salad_roma.refine_pose_with_roma(
        str(dummy_query),
        np.zeros((3, 1)), np.zeros((3, 1)),
        np.eye(3), 8, 8,
        np.zeros((1, 3)), np.zeros((1, 3)),
        out_dir=str(out),
        matcher_type="nonexistent_matcher",
    )

    assert result["success"] is False
    assert "matcher_type" in result.get("error", "")
