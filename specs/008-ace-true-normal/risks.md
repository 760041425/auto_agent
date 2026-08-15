# 008 风险与对策

| RISK-ID | 风险 | 等级 | 缓解 |
| --- | --- | --- | --- |
| RISK-008-01 | DSINE/MiDaS 权重下载源不可达或模型加载失败（离线环境/网络） | 中 | `estimate_normal` 懒加载 + 加载失败优雅回退常量 0.5 并标注 `normal_source="constant_fallback"`（不崩溃）；权重路径可配置 |
| RISK-008-02 | 真法线推理在 MPS/CPU 上过慢（单图 >10s），影响 ace_better（现 ~2s） | 中 | 抽样试跑测耗时；必要时降采样输入（法线仅需中等分辨率）+ 缓存；基准记录耗时指标纳入决策 |
| RISK-008-03 | 真法线路径基准不显著优于常量/3ch（法线信息增益有限） | **已发生（P3 确认）** | 6ch_midas 真法线 mean_distance 1.197m 差于 6ch_constant 0.877m → 触发预案：**维持 007 现状路由**，数据记录于 reports/benchmark_008.json 与 D-008-03；这不是失败，是决策依据 |
| RISK-008-04 | 基准查询集不足 20 张或真值不全，统计不可靠 | 中 | tile 渲染图覆盖（363 accepted tiles 有 camera_pose 真值）；抽样 ≥20 张，明确缺陷 |
| RISK-008-05 | 改 `resolve_ace_model` 路由影响 007 已验证行为（scene 3ch 优先） | 低 | 切换仅在基准显著胜出时；TL-007-01/02 回归兜底 + AC-008-05 |
| RISK-008-06 | 外部模型引入的依赖（权重下载、新库）污染 poetry/requirements | 低 | 权重懒加载不依赖重库；DSINE 若需其官方 repo 仅限推理调用，不新增硬依赖（或评估 MiDaS torchvision 通道） |