# 007 决策记录

| D-ID | 决策 | 理由 | 替代方案 | 状态 |
| --- | --- | --- | --- | --- |
| D-007-01 | 法线 skew 修复策略 =「**场景 3ch 模型优先（RGB-only，无 skew）；回退 6ch + 常量 0.5 占位**」，而非「推理期接 DSINE/MiDaS 真法线」 | ① 8/9 已有自洽 3ch 场景模型（`train_ace_rgb`/`ACERegressor3Ch`），`train_ace`/`ace_rgb_only` 已验证可跑通，立即可用；② 推理期真法线需外部模型权重 + 精度基准 = 治本 D，用户明确最后再做；③ 常量 0.5 ≈ 训练真法线映射后分布均值（[0.4,0.6] 中心），是无信息占位，远优于梯度噪声 | 推理期 DSINE/MiDaS 真法线 → 列为 RISK-007-01 后续规格 | 采用 |
| D-007-02 | `ace_better`/`ace_normal` 名称与 tag 兼容保留（`ace_better_normal`/`ace_normal`），但语义改为「优先场景 RGB-only」；“更好法线”名存实亡，`_estimate_normal_dsine` 更名 `_estimate_gradient_normal` 并降级为 debug 输入 | 避免破坏 registry/前端对 tag 的既有绑定；诚实命名防误导（无真 DSINE 模型）；梯度法线不再作为默认推理输入 | 直接删函数 —— tag 兼容成本高，且 debug 对比仍有用 | 采用 |
| D-007-03 | PnP 失败诊断进公开返回结构 `diagnostics.*`（而非仅日志） | 「空间感」从玄学变可量化：前端可见 best inliers / 预测 3D Z 范围 vs LAS bbox 重叠率；仅**新增**顶层字段，不改变既有 `error/tag/elapsed/pose/quality` 语义，006 归一化与 SALAD 消费方不受影响 | 仅日志输出 → 用户侧仍不可观测，与 C 目标冲突 | 采用 |
| D-007-04 | `solve_pnp_with_focal_search` 只增 `attempts_summary` 字段，不改既有返回键与成功/失败语义 | 该函数被 SALAD 系等多处调用，扩容需回归兜底（TL-007-07）；内部已有焦距候选统计（日志「尝试 15 次」），仅需带出 | 新建独立诊断函数 → 重复 PnP 逻辑，成本高 | 采用 |
| D-007-05 | `ace_with_normal:87` / `ace_rgb_only:199` 的 `result` 未定义引用（copy-paste 死代码）**一并修** | 扩散排查纪律：同文件、同模式、本次必达；不修则低点分支 500 而非优雅失败 | 记录 issue 另开 PR —— 同文件必达且属本次失败路径验收前置（AC-007-05） | 采用 |
| D-007-06 | 前端失败诊断采用 Python 镜像测试（沿用 005/006 先例），JS 真机验收留人工 | 项目无 Node/JS 测试运行时；005/006 已验证该模式 | 引入 Node harness —— 改变工程拓扑，超范围 | 采用 |