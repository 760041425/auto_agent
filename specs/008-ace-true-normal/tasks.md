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
| [ ] | TASK-008-08 | TASK-008-07 | 抽样试跑 DSINE vs MiDaS（5 张图），记录选型入 decisions | 试跑记录 |
| [ ] | TASK-008-09 | TASK-008-08 | 失败测试 TL-008-04：基准运行器四路径对比 | pytest red |
| [ ] | TASK-008-10 | TASK-008-09 | 基准运行器（≥20 张，落 reports/benchmark_008*.json） | pytest green（TL-008-04） |
| [ ] | TASK-008-11 | TASK-008-10 | TL-008-05：依据基准数据更新 `resolve_ace_model` 路由或维持 + 单测同步 | pytest green（TL-008-05） |
| [ ] | TASK-008-12 | TASK-008-11 | TL-008-06：回归（007 路由）+ 全量门禁 | 三脚本 + 全量 pytest 绿 |
| [ ] | TASK-008-13 | TASK-008-12 | 同步任务/清单/测试状态 + commit + push（PR 处置沿用 006 阻塞约定） | git log / status |