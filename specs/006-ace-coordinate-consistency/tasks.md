# 006 实施任务

| 状态 | TASK-ID | 依赖 | 任务 | 完成证据 |
| --- | --- | --- | --- | --- |
| [x] | TASK-006-01 | 无 | 新建 `specs/006-ace-coordinate-consistency/` 八件套 | 文件存在，validate-specs 通过 |
| [x] | TASK-006-02 | TASK-006-01 | 新增后端契约失败测试（TL-006-01）：无 coordinate_transform 的 ACE raw → not_available + consistency + inliers 正确 | pytest red |
| [x] | TASK-006-03 | TASK-006-02 | 修改 `contracts.py` coordinate_transform fallback：补 `consistency.status=not_available` + 明确 reason | pytest green（TL-006-01） |
| [x] | TASK-006-04 | TASK-006-03 | 新增 train_ace 覆写归一化失败测试（TL-006-02，mock 训练） | pytest red |
| [x] | TASK-006-05 | TASK-006-04 | 重构 `registry.py` `_run_train_ace` 覆写走归一化（抽共享 helper，与 `_append_result` 对齐） | pytest green（TL-006-02） |
| [x] | TASK-006-06 | TASK-006-05 | 前端镜像失败测试（TL-006-03/04/05）：badge 无 fallback、reason 文案、诊断行真值 | pytest red |
| [x] | TASK-006-07 | TASK-006-06 | 修改 `web/app_v10.js`：`localizeStatusBadge` 去 fallback、判定卡 reason 映射、诊断行字段回退 | pytest green（TL-006-03/04/05） |
| [x] | TASK-006-08 | TASK-006-07 | 集成测试 TL-006-06（mock 训练 + task 完成覆写 → 接口返回含 coordinate_transform.not_available） | pytest green |
| [x] | TASK-006-09 | TASK-006-08 | 同步文档（ubiquitous-language / context-map / spatial-localization README） | drift-check 通过 |
| [x] | TASK-006-10 | TASK-006-09 | 全量门禁：validate-specs + run-all-tests fast + drift-check | 三脚本全绿 |
| [ ] | TASK-006-11 | TASK-006-10 | 更新任务/清单/测试状态并 commit + push + PR | git log / PR 可见 |