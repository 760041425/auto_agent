"""FAISS 替换 numpy 余弦检索原型（SALAD v2 检索加速）。

验证目标：
  - 当前实现：np.dot 暴力，2732 条候选，~5-20ms
  - FAISS 目标：IndexFlatIP + L2 归一化，<1ms（CPU），GPU 更快
  - 精度等价：归一化后内积 == 余弦相似度

使用方式：
  1. 索引构建（离线，缓存）：build_faiss_index(descriptors_npy)
  2. 在线检索：faiss_search(query_desc, k=5)

依赖：pip install faiss-cpu  # 或 faiss-gpu
"""
import numpy as np

try:
    import faiss
    HAS_FAISS = True
except ImportError:
    HAS_FAISS = False


class FaissSALADIndex:
    """SALAD v2 对称检索的 FAISS 后端。"""

    def __init__(self, dim: int = 768, use_gpu: bool = False):
        """dim: DINOv2 descriptor 维度（768 for ViT-S/14）。"""
        if not HAS_FAISS:
            raise ImportError("faiss not installed. pip install faiss-cpu")
        self.dim = dim
        self.use_gpu = use_gpu
        self.index = None
        self.keys: list[str] = []

    def build(self, descriptors: dict[str, np.ndarray]):
        """从 {tile_key: rgb_desc} 构建索引。

        descriptors: dict，value 为 (D,) float32 归一化描述子
        """
        self.keys = list(descriptors.keys())
        mat = np.stack([descriptors[k] for k in self.keys]).astype(np.float32)
        # L2 归一化 → 内积 == 余弦相似度
        faiss.normalize_L2(mat)

        self.index = faiss.IndexFlatIP(self.dim)
        if self.use_gpu:
            res = faiss.StandardGpuResources()
            self.index = faiss.index_cpu_to_gpu(res, 0, self.index)
        self.index.add(mat)
        return self

    def search(self, query_desc: np.ndarray, k: int = 5):
        """检索 top-k 最近邻。

        返回: list[(tile_key, similarity)]，按相似度降序
        """
        if self.index is None:
            raise RuntimeError("index not built")
        q = query_desc.astype(np.float32).reshape(1, -1)
        faiss.normalize_L2(q)
        scores, indices = self.index.search(q, k)
        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self.keys):
                continue
            results.append((self.keys[idx], float(score)))
        return results


# ── 与 salad_roma_v2 集成示例 ──
def _salad_retrieve_v2_faiss(q_desc, top_k=5):
    """替换 _salad_retrieve_v2 中的 np.dot 暴力。

    前提：全局维护一个 FaissSALADIndex 实例（类似 _SALAD_INDEX_V2 缓存）。
    """
    global _FAISS_SALAD_INDEX
    if _FAISS_SALAD_INDEX is None:
        # 首次调用：从 _SALAD_INDEX_V2 构建
        from services.localizer.salad_roma_v2 import _ensure_index
        raw_index = _ensure_index()  # {key: {"rgb":..., "multi":...}}
        desc_dict = {k: v["rgb"] for k, v in raw_index.items()
                     if isinstance(v, dict) and "rgb" in v}
        _FAISS_SALAD_INDEX = FaissSALADIndex(dim=len(next(iter(desc_dict.values()))))
        _FAISS_SALAD_INDEX.build(desc_dict)
    return _FAISS_SALAD_INDEX.search(q_desc, k=top_k)


_FAISS_SALAD_INDEX: FaissSALADIndex | None = None
