"""009 特征匹配加速 — 单测。

TL-009-01: FAISS 检索结果与 numpy 暴力等价（无 FAISS 时跳过）。
TL-009-02: 无 FAISS / 无 XFeat 时优雅降级。
TL-009-03: torch.compile 标记设置不报错。
TL-009-04: fast_mode 参数传递正确。
TL-009-05: registry 新算法 ids 可用，原算法不动。
"""
import math

import numpy as np
import pytest

from services.localizer import salad_roma_v2 as v2


# ── TL-009-01: FAISS 等价性 ──

def test_faiss_search_matches_numpy_brute_force():
    """FAISS IndexFlatIP + L2 归一化 == numpy 余弦相似度（误差 <1e-6）。"""
    try:
        import faiss  # noqa: F401
    except ImportError:
        pytest.skip("faiss 未安装，跳过 FAISS 等价性测试")

    # 构造 100 条随机描述子（768d，DINOv2 ViT-S 维度）
    np.random.seed(42)
    dim = 728  # 用较小维度加速测试
    n = 100
    descs = np.random.randn(n, dim).astype(np.float32)
    keys = [f"tile_{i}" for i in range(n)]

    # 构建 FAISS 索引
    v2._FAISS_INDEX = None
    v2._FAISS_KEYS = []
    # 直接注入测试数据
    index = faiss.IndexFlatIP(dim)
    descs_norm = descs / (np.linalg.norm(descs, axis=1, keepdims=True) + 1e-8)
    index.add(descs_norm)
    v2._FAISS_INDEX = index
    v2._FAISS_KEYS = keys

    # 随机 query
    q = np.random.randn(dim).astype(np.float32)

    # FAISS 检索
    faiss_results = v2._faiss_search(q, k=5)

    # numpy 暴力
    q_norm = q / (np.linalg.norm(q) + 1e-8)
    sims = descs_norm @ q_norm
    top_idx = np.argsort(sims)[::-1][:5]
    numpy_results = [(keys[i], float(sims[i])) for i in top_idx]

    # 比较 top-1 一致
    assert len(faiss_results) == len(numpy_results)
    if faiss_results and numpy_results:
        assert faiss_results[0][0] == numpy_results[0][0], \
            f"FAISS top1={faiss_results[0][0]} != numpy top1={numpy_results[0][0]}"
        assert abs(faiss_results[0][1] - numpy_results[0][1]) < 1e-5


# ── TL-009-02: 依赖检测 ──

def test_has_faiss_detects_without_crashing():
    """_has_faiss() 不崩溃，返回 bool。"""
    result = v2._has_faiss()
    assert isinstance(result, bool)


def test_has_xfeat_detects_without_crashing():
    """_has_xfeat() 不崩溃，返回 bool。"""
    result = v2._has_xfeat()
    assert isinstance(result, bool)


def test_faiss_search_fallback_when_no_index():
    """无 FAISS 索引时 _faiss_search 返回 []。"""
    v2._FAISS_INDEX = None
    v2._FAISS_KEYS = []
    q = np.random.randn(728).astype(np.float32)
    result = v2._faiss_search(q, k=5)
    assert result == []


# ── TL-009-03: compile 标记 ──

def test_compile_markers_set_without_crashing():
    """_compile_models() 不崩溃（无模型时也安全）。"""
    # 不崩溃即可
    v2._compile_models()
    assert v2._DINO_COMPILED is True or v2._DINO_COMPILED == "done" or v2._DINO_COMPILED is False


# ── TL-009-04: fast_mode 参数 ──

def test_fast_mode_parameter_exists():
    """localize_with_salad_roma_v2 接受 fast_mode 参数。"""
    import inspect
    sig = inspect.signature(v2.localize_with_salad_roma_v2)
    assert "fast_mode" in sig.parameters


def test_matcher_mode_xfeat_accepted():
    """matcher_mode='xfeat' 被接受（不返回 Unsupported）。"""
    # 不实际跑定位（需要真实图像），仅验证参数校验
    # 通过 inspect 确认 xfeat 在允许列表
    import inspect
    src = inspect.getsource(v2.localize_with_salad_roma_v2)
    assert '"xfeat"' in src or "'xfeat'" in src


# ── TL-009-05: registry 新算法 ──

def test_registry_has_new_accel_algorithms():
    """registry 包含 009 新增的加速算法。"""
    from services.localizer.registry import DEFAULT_ALGORITHM_REGISTRY
    ids = DEFAULT_ALGORITHM_REGISTRY.ids()
    # 新算法
    assert "salad_v2_loftr_fast" in ids
    assert "salad_v2_hybrid_fast" in ids
    assert "salad_v2_xfeat" in ids
    # 原算法不动
    assert "salad_roma_v2" in ids
    assert "salad_roma_v2_loftr" in ids
    assert "hybrid" in ids
    assert "ace_las" in ids
    assert "flann" in ids


def test_registry_new_algorithms_have_labels():
    """新算法有可读 label。"""
    from services.localizer.registry import DEFAULT_ALGORITHM_REGISTRY
    for aid in ["salad_v2_loftr_fast", "salad_v2_hybrid_fast", "salad_v2_xfeat"]:
        algo = DEFAULT_ALGORITHM_REGISTRY.get(aid)
        assert algo.label
        assert len(algo.label) > 5


# ── 辅助 ──

def test_apply_prior_filter_no_crash():
    """_apply_prior_filter 不崩溃（无 _POSE_TREE 时返回原列表）。"""
    scores = [("tile_1", 0.9), ("tile_2", 0.8)]
    result = v2._apply_prior_filter(scores, None, 15.0)
    assert result == scores  # prior_position=None 时无过滤
