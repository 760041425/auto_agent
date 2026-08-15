# 009 决策记录

| D-ID | 决策 | 理由 | 替代方案 | 状态 |
| --- | --- | --- | --- | --- |
| D-009-01 | 原算法不动，新增 `*_fast` / `xfeat` 变体 | 用户要求方便对比；避免引入回归风险 | 直接改原算法 → 无法对比 | 采用 |
| D-009-02 | FAISS 可选依赖，无则回退 numpy | 避免硬依赖；项目当前无 FAISS | 强制依赖 FAISS → 增加安装成本 | 采用 |
| D-009-03 | XFeat 未安装则不注册该算法 | 避免 ImportError 影响其他算法 | 注册但运行时失败 → 体验差 | 采用 |
| D-009-04 | torch.compile 失败回退 eager | MPS compile 偶有不兼容 op | 强制 compile → 可能崩溃 | 采用 |
| D-009-05 | 加速前后 benchmark 对比 ≥5 张图 | 数据决策，不凭感觉 | 仅测 1 张 → 统计不可靠 | 采用 |
| D-009-06 | 描述子 PCA 降维暂不做（本期） | 风险较高，需重测检索精度；留 follow-up | 本期做 → 工作量大 | 本期不做 |
| D-009-07 | `salad_v2_loftr_fast` 升级为默认 LoFTR 方案 | benchmark 12 查询：成功率 100% = 原版，延迟 12.33s = 原版 20.32s × 0.61（≤0.7 目标）；前端 `checked`，原版标"高精度对照" | 保留原版默认 → 放弃 1.65x 加速收益 | 采用 |
| D-009-08 | hybrid / loftr fast 加自适应回退（fast 失败→原版 top_k=3+迭代） | hybrid_fast 92% 成功率，8% 因 top_k=1 漏检；回退仅触发失败案例，均延迟≈13.6s<15s 目标，成功率预计→~100%。**验收通过**：`reports/benchmark_009_v2.json` 12 查询 hybrid_fast 成功率 100%（12/12），均延迟 14.56s < 15s 目标 | 纯 top_k=1 → 8% 失败不可接受；取消 fast → 失去加速 | 采用 ✅ 已验收 |
| D-009-09 | XFeat 验证完成（vismatch.xFeatMatcher） | 通过 `gmberton/image-matching-models` (vismatch) + `verlab/accelerated_features` 子模块安装。跨域匹配数 646 vs DISK+LG 143（4.5×），置信度 0.186 vs 0.005（37×）。benchmark 12 查询成功率 **100%**，均延迟 16.14s。接口更新为 `xFeatMatcher(device, mode="sparse")._forward()` | 放弃 XFeat → 失去潜在 DISK+LG 替代方案 | **已验证 ✅**：跨域显著优于 DISK+LG，成功率与 LoFTR 持平；延迟无优势，定位为 domain-gap 敏感场景的稀疏匹配前端 |
