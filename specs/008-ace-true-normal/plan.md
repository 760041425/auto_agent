# 008 实施计划

## 批次划分（单流水线，顺序执行；一次一个可验证行为）

### 批次 P0：法线估计模块（TL-008-01/02）
1. TL-008-01 红：`estimate_normal` 接口测试（注入假 DSINE 输出 → 断言 [0,1] 映射 `(n+1)*0.5`、尺寸一致）。红：模块不存在。
2. 绿：新建 `services/localizer/normal_estimator.py`（懒加载 DSINE/MiDaS、`estimate_normal(image) -> [0,1] float32`）。
3. TL-008-02 红：权重缺失/加载失败 → 回退常量 0.5、`normal_source="constant_fallback"` 不崩溃。
4. 绿：降级分支。

### 批次 P1：接入 runner（TL-008-03）
5. 红：`ace_better`（6ch 路径）`normal_mode="dsine"`（mock 估计器）→ predict_dense 收到真法线；`"constant"` 与 007 一致。
6. 绿：`ace_better`/`ace_normal` 增加 `normal_mode`，6ch 路径选择法线来源。

### 批次 P2：抽样试跑 + 选型（TASK-008-08）
7. 5 张图（tile 渲染图）跑 DSINE vs MiDaS：记录输出质量（视觉/法线合理度）+ 耗时（MPS/CPU）→ 更新 D-008-01 终选。

### 批次 P3：基准 + 路由决策（TL-008-04/05/06）
8. TL-008-04 红：基准运行器测试（四路径对比表字段断言）。
9. 绿：基准运行器（≥20 张：真实查询图 + tile 图；指标 success 率/LAS 验证率/mean_distance/reproj/inliers；落 `reports/benchmark_008*.json`）。
10. 运行基准 → 数据看板 → TL-008-05：满足切换条件（D-008-03）→ 更新 `resolve_ace_model` 默认 + TL-007-01/02 同步；否则维持并文档记录。
11. TL-008-06：007 默认路由回归 + 三脚本 + 全量 pytest。

## 验证方法

- Red→Green：每次改动用 `pytest` 验证，红输出留证。
- 单元测试全部 mock 法线估计器；真实权重仅抽样试跑与基准（运行产物，不入库）。
- 基准结论同步进 `specs/008 decisions`（D-008-01/03 定案）。
- 前端无改动（007 的失败诊断行已覆盖可观测性；008 不动前端，除非路由切换需文案标注）。