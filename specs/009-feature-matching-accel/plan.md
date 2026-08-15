# 009 实施计划

## 批次划分

### 批次 P0：规格包（已完成）
- 八件套建齐

### 批次 P1：首选 — MPS+FAISS 加速（独立可验证）
1. **FAISS 检索后端**：`services/localizer/salad_roma_v2.py` 新增 `_salad_retrieve_v2_faiss`，try-import faiss，无则回退 numpy
2. **MPS 加速**：DINOv2 / LoFTR 推理包裹 `torch.compile` + `torch.autocast(mps, fp16)`，失败回退 eager
3. **参数收紧**：新增 `fast_mode` 参数（top_k=1, max_iterations=1, 先验强制开）
4. **注册新算法**：`registry.py` 新增 `salad_v2_loftr_fast` / `salad_v2_hybrid_fast`
5. **单测**：FAISS 等价性 + 加速后成功率不降低

### 批次 P2：备选 A — XFeat 替换 DISK+LG（独立可验证，与 P1 并行）
1. **XFeat 匹配器**：新增 `_match_tile_with_xfeat`（try-import xfeat）
2. **注册新算法**：`salad_v2_xfeat`（XFeat + DINOv2 检索）
3. **单测**：XFeat 输出格式正确（kpts_q, kpts_t, cert）
4. **未安装时**：不注册，前端不展示

### 批次 P3：备选 B — 批量匹配 + 异步流水线（独立可验证，与 P1/P2 并行）
1. **批量 LoFTR**：多个候选 tile 合并为 batch 送 LoFTR
2. **异步检索+匹配**：线程池并行
3. **注册新算法**：`salad_v2_loftr_batch`

### 批次 P4：前端对比 UI
1. `localizeAlgorithmNames` 新加 `salad_v2_loftr_fast` / `salad_v2_hybrid_fast` / `salad_v2_xfeat` / `salad_v2_loftr_batch`
2. 原 key 文案/行为不变
3. 新选项 label 加 `[加速]` / `[XFeat]` / `[批量]` 标识

### 批次 P5：基准对比 + 门禁
1. 运行 benchmark（原方案 vs 新方案，≥5 张图）
2. 全量门禁绿

## 验证方法

- 每个批次独立 Red → Green → Refactor
- 原算法代码 diff 为空（不修改）
- 新算法注册动态检测依赖（无则不注册）
- benchmark 对比报告落 `reports/benchmark_009_*.json`
