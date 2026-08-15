# 009 风险与对策

| RISK-ID | 风险 | 等级 | 缓解 |
| --- | --- | --- | --- |
| RISK-009-01 | torch.compile 在 MPS 上对 LoFTR/DINOv2 某些 op 不兼容 | 中 | try/except 回退 eager；记录哪些 op fallback |
| RISK-009-02 | FAISS 安装失败（编译问题 / 无 wheel） | 低 | try-import 回退 numpy；FAISS 为可选依赖 |
| RISK-009-03 | XFeat 未安装导致算法缺失 | 低 | 动态检测，未注册则前端不展示 |
| RISK-009-04 | FP16 导致精度下降（LoFTR confidence 分布偏移） | 中 | benchmark 对比；关键路径保留 FP32 |
| RISK-009-05 | 批量 LoFTR 显存/内存不足（MPS 统一内存） | 中 | 限制 batch size（≤3）；内存超则回退串行 |
| RISK-009-06 | top_k=1 在先验失败时漏掉正确候选 | 中 | 先验失败自动回退 top_k=3（graceful） |
| RISK-009-07 | 原算法被意外修改 | 低 | 代码 review + diff 检查；单测覆盖原算法行为 |
