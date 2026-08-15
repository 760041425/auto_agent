# 009 特征匹配加速 + 多方案并行对比

状态：进行中（P0 规格包已建；P1 MPS+FAISS 首选 + P2 XFeat 备选 + P3 批量异步 并行推进）
上下文：视觉定位特征匹配、SALAD v2 检索、LoFTR/DISK+LightGlue 匹配、MPS 加速、工程优化

## 背景与目标

### 背景

specs 研究 `research/2026-05-12-feature-matching-success-analysis-and-acceleration.md` 已结论：
- **仅 LoFTR 系（salad_roma_v2_loftr / hybrid）100% 成功**，其他方法因稀疏关键点跨域枯竭失败
- 当前最优 `salad_roma_v2_loftr` avg latency **1.88s**，Hybrid **4.84s**
- 5 维度 15 条加速建议，推荐首选 **MPS 工程加速 + FAISS 检索**（0.5-1 人天，零精度风险，预期 1.88s → 0.6-1.0s）

### 目标

1. **首选方案**：MPS 工程加速（torch.compile + FP16 + 减少 top_k/iterations）+ FAISS 替代 numpy 余弦检索
2. **备选方案 A**：XFeat 替换 DISK+LightGlue（作为 SALAD v2 默认稀疏匹配器）
3. **备选方案 B**：批量匹配 + 异步流水线（利用 MPS 并行性）
4. **网页对比**：新增加速方案选项，**原方案不修改**，用户可并排对比精度/延迟

## 范围

### In Scope

- `services/localizer/salad_roma_v2.py`：MPS 加速（torch.compile、FP16）、FAISS 检索后端
- `services/localizer/registry.py`：新增 `salad_v2_loftr_fast` / `salad_v2_hybrid_fast` / `salad_v2_xfeat` 算法（**不修改原有** `salad_roma_v2` / `salad_roma_v2_loftr` / `hybrid`）
- `web/app_v10.js`：`localizeAlgorithmNames` 新增加速选项（**不修改原有 key**）
- `services/localizer/pose_utils.py`：可选加速（FAISS 不触及 PnP，保持 solve_pnp_with_focal_search 不变）

### Out of Scope

- 不改 `solve_pnp_with_focal_search` 返回键名/语义（SALAD 系依赖，硬红线）
- 不重训 ACE 模型
- 不用 Sobel 梯度伪法线作默认 6ch 推理输入
- 不修改原始算法（salad_roma_v2 / salad_roma_v2_loftr / hybrid / ace_*）的实现
- 不提交 projections/ reports/ query_images/ las/ 运行产物

## 验收标准

| AC-ID | 标准 | 验证 |
| --- | --- | --- |
| AC-009-01 | 新增 ≥3 个加速算法（fast_loftr / fast_hybrid / xfeat），原算法不动 | `registry.py` ids 包含新旧；原算法代码 diff 为空 |
| AC-009-02 | fast_loftr 延迟 ≤ 原 loftr 的 70%（同硬件同查询集） | benchmark 对比报告 |
| AC-009-03 | fast_loftr 成功率 ≥ 原 loftr（不降低精度） | benchmark 对比报告 |
| AC-009-04 | FAISS 检索结果与 numpy 暴力等价（余弦相似度误差 <1e-6） | 单测断言 |
| AC-009-05 | 前端新增加速选项，原选项文案/行为不变 | Playwright/DOM 验证 |
| AC-009-06 | 全量门禁绿（validate-specs / run-all fast / drift-check / pytest） | 三脚本 |
