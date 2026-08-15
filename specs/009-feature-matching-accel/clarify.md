# 009 澄清记录

| CL-ID | 问题 | 结论 | 确认方式 | 影响范围 |
| --- | --- | --- | --- | --- |
| CL-009-01 | 加速方案是否修改原算法？ | **不修改**。原 `salad_roma_v2` / `salad_roma_v2_loftr` / `hybrid` 保留不变，新增 `*_fast` / `xfeat` 变体 | 用户明确要求「原方案不要修改，方便对比」 | registry + 前端 |
| CL-009-02 | FAISS 是否必须？环境是否已有？ | 当前无 FAISS。首选方案 FAISS 为可选依赖：有则加速检索，无则回退 numpy（graceful fallback） | 代码实现时 try-import | salad_roma_v2 检索 |
| CL-009-03 | XFeat 是否已安装？ | 未安装。备选方案 A 需 `pip install xfeat` 或 clone verlab/XFeat；未安装时该算法不注册（try-import） | 实现时检测 | registry 动态注册 |
| CL-009-04 | torch.compile 在 MPS 是否稳定？ | PyTorch 2.x MPS 后端已较稳定；compile 失败时回退 eager（try/except） | 实现时检测 | LoFTR/DINOv2 推理 |
| CL-009-05 | 批量匹配是否改变精度？ | 不改变。batch 推理数学等价于串行；仅改变调用方式 | 单测对比 | LoFTR 推理 |
| CL-009-06 | 前端如何展示新旧对比？ | 新 key 加 `_fast` / `_xfeat` 后缀，label 加 `[加速]` / `[XFeat]` 标识；原 key 文案不变 | 前端改动 | app_v10.js |
