# 007 澄清记录

| CL-ID | 问题 | 结论 | 确认方式 | 影响范围 |
| --- | --- | --- | --- | --- |
| CL-007-01 | 训练期法线通道到底是什么？是否真的「常量 0.5」？ | **不是常量**。363 个 accepted tiles 全部带真实 `normal_path`（如 `projections/tiles/*_normal.npy`，值域 [-1,1]，dtype float32），训练时加载后 `(n+1)*0.5` 映射到 [0,1]。真法线分量 absmean≈0.19 → 映射后分布均值≈0.5。早期「常量 0.5」假设基于 `ace_trainer.py:100` 的 `normal=zeros` 默认值，但该默认只在 `normal_path` 缺失时生效。 | 代码（ace_trainer.py:99-106）+ 查证 tile_index.json（363/363 带 normal 文件）+ 读取 normal.npy 值域 | 根因结论修正、A 方案细节 |
| CL-007-02 | 为何不直接上 DSINE/MiDaS 真法线解决 skew？ | 用户拍板「先 A+B+C，不行最后再做 D」。DSINE 引入外部模型权重 + 6ch 真法线路径精度基准 = 治本（后续规格，RISK-007-01）。本规格以「3ch 场景模型优先 + 常量 0.5 占位回退」治标。 | 用户决策（「先做 A+B+C 不行最后再做D」） | 范围界定 |
| CL-007-03 | 常量 0.5 占位 vs 训练真法线仍是不同分布，靠谱吗？ | 是「无信息占位」而非「精确对齐」：真法线映射后集中在 [0.4,0.6]，0.5 与分布均值/众数一致，**不会注入高频错误信息**（远优于梯度噪声）；且回退路径只在无场景 3ch 模型时启用。诚实标注 `normal_source: constant_fallback`，不伪装成真法线。 | 分布统计 + D-007-01 | 回退路径行为 |
| CL-007-04 | `ace_model_scene.pth` 不在版本控制（运行产物），路由依赖存在性是否可靠？ | 可靠。路由基于文件存在性 + 通道自动检测（`_detect_architecture`）：存在 → 3ch RGB-only；缺失 → 回退 6ch 常量占位（行为降级而非崩溃）。新环境未跑过 `train_ace` 时自动落回退路径。 | 运行产物约定（AGENTS.md：`projections/` 不入库） | 模型路由 |
| CL-007-05 | 更名 `_estimate_normal_dsine` 会不会破坏其他调用方？ | 已 grep：该函数只在 `enhanced_ace.py` 内部（`ace_with_better_normal`）被调用；`_estimate_normal_simple` 只在 `ace_localizer.py` 的 `ace_with_normal` 被调用。更名 + debug-only 无外部依赖。 | grep 扩散排查 | 命名重构 |
| CL-007-06 | `ace_with_normal:87` / `ace_rgb_only:199` 的 `result['coords_3d']` 引用是本次新增问题吗？ | 否，是既有 copy-paste 死代码（触发即 NameError）。但与本次同文件、同模式，符合「扩散排查命中同类 → 一并修」纪律。 | 代码审读 + 扩散排查 | 死代码修复 |