# 005 实施任务

| 状态 | TASK-ID | 依赖 | 任务 | 完成证据 |
| --- | --- | --- | --- | --- |
| [ ] | TASK-005-01 | 无 | 新建 `specs/005-coordinate-transform-fix/` 八件套 | 文件存在，validate-specs 通过 |
| [ ] | TASK-005-02 | TASK-005-01 | 新建 `tests/test_frontend_coordinate_display.py`，写 5 个失败测试 | 文件存在 |
| [ ] | TASK-005-03 | TASK-005-02 | 跑测试，确认全部红 | pytest 输出 5 failed |
| [ ] | TASK-005-04 | TASK-005-03 | 修改 `web/app_v10.js` 的 `verifyCoordinatePoint` 函数 | 代码修改完成 |
| [ ] | TASK-005-05 | TASK-005-04 | 跑测试，确认全部绿 | pytest 输出 5 passed |
| [ ] | TASK-005-06 | TASK-005-05 | 跑全套门禁 | validate-specs + run-all-tests + drift-check 通过 |
| [ ] | TASK-005-07 | TASK-005-06 | commit + push | git log 可见 |
