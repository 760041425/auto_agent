# 008 实施任务

| 状态 | TASK-ID | 依赖 | 任务 | 完成证据 |
| --- | --- | --- | --- | --- |
| [ ] | TASK-008-01 | 无 | 新建 `specs/008-ace-true-normal/` 八件套 | 文件存在，validate-specs 通过 |
| [x] | TASK-008-02 | TASK-008-01 | 失败测试 TL-008-01：`estimate_normal` 接口+映射 | pytest red → green |
| [x] | TASK-008-03 | TASK-008-02 | `services/localizer/normal_estimator.py`：DSINE/MiDaS 封装 + `(n+1)*0.5` 映射 + 懒加载 | pytest green（TL-008-01） |
| [x] | TASK-008-04 | TASK-008-03 | 失败测试 TL-008-02：权重缺失回退常量 0.5 | pytest red → green |
| [x] | TASK-008-05 | TASK-008-04 | 降级回退 + `normal_source` 标注 | pytest green（TL-008-02） |
| [x] | TASK-008-06 | TASK-008-05 | 失败测试 TL-008-03：`normal_mode` 参数 + 6ch 真法线输入 | pytest red → green |
| [x] | TASK-008-07 | TASK-008-06 | 接入 `ace_better`/`ace_normal`（`normal_mode`，6ch 路径） | pytest green（TL-008-03） |
| [x] | TASK-008-08 | TASK-008-07 | 抽样试跑 DSINE vs MiDaS（5 张图），记录选型入 decisions | 定案 MiDaS（DSINE 21GB+GDrive 确认墙不可达）；``_load_model`` 由桩改真实 |
| [x] | TASK-008-09 | TASK-008-08 | 失败测试 TL-008-04：基准运行器四路径对比 | pytest red → green |
| [x] | TASK-008-10 | TASK-008-09 | 基准运行器（≥20 张，落 reports/benchmark_008*.json） | reports/benchmark_008.json |
| [x] | TASK-008-11 | TASK-008-10 | TL-008-05：依据基准数据更新 `resolve_ace_model` 路由或维持 + 单测同步 | 维持 007 现状（数据决策） |
| [x] | TASK-008-12 | TASK-008-11 | TL-008-06：回归（007 路由）+ 全量门禁 | validate-specs 绿 / run-all fast 124 passed / drift-check 0 err / 全量 129 passed（仅 las/points3D.txt 既有 baseline 红，008 外） |
| [x] | TASK-008-13 | TASK-008-12 | 同步任务/清单/测试状态 + commit + push（PR 处置沿用 006 阻塞约定） | c14b641 |