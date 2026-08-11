# 008 澄清记录

| CL-ID | 问题 | 结论 | 确认方式 | 影响范围 |
| --- | --- | --- | --- | --- |
| CL-008-01 | D 何时启动？007 真验说 PnP 已不失败，为何还要做？ | 用户拍板「先 A+B+C，不行最后再做 D」；007 真验（d7393932，2026-08-11）显示精度天花板仍低：LAS mean_distance 0.52~1.84m、reproj 600~780px、scene 3ch 两次运行 pose z 差 ~20m——「不行」条件触发，D 立项。 | 真实图三路径对比（见 spec 背景表） | 启动条件 |
| CL-008-02 | 推理真法线选 DSINE 还是 MiDaS？ | 首选 DSINE（end-to-end 法线，输出直观、训练数据域相近），备选 MiDaS 深度→法线（工程成熟、权重小）。**最终在 TDD 前先做一次 5 张图抽样试跑**，以输出质量与运行时长定选型（记入 decisions）。 | 抽样试跑数据 | 法线估计模块 |
| CL-008-03 | 法线值域/映射怎么对齐训练？ | 训练 `SceneCoordinateDataset`：加载 normal.npy（[-1,1]）→ `(n+1)*0.5` → [0,1] 喂 6ch。推理 DSINE 输出通常已是 [-1,1] 或需归一，`estimate_normal` 统一返回 [0,1]，与训练映射一致。 | 代码（ace_trainer.py:106） | 接口契约 |
| CL-008-04 | 基准查询集与位姿真值从哪来？ | 查询集：`query_images/` 现有真实图（≥20 张，含 d7393932/692cdeec）+ `projections/tiles/` accepted tile 渲染图（场景覆盖广、有 camera_pose 真值）。真值：tile 的 `camera_pose` / colmap 位姿；查询图无真值仅 LAS 验证。 | 数据核查（tile_index.json 字段） | 基准范围 |
| CL-008-05 | 基准失败时（真法线不显著胜出）怎么办？ | 维持 007 现状路由（scene 3ch 优先），文档记录数据与理由；6ch 真法线保留为可选 `normal_mode`。不是「必须切换」，是「以数据决策」。 | 用户拍板原则（D 为治本探索） | 路由决策 |