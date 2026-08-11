# 007 实施任务

| 状态 | TASK-ID | 依赖 | 任务 | 完成证据 |
| --- | --- | --- | --- | --- |
| [x] | TASK-007-01 | 无 | 新建 `specs/007-ace-better-fix/` 八件套 | 文件存在，validate-specs 通过 |
| [x] | TASK-007-02 | TASK-007-01 | 失败测试 TL-007-01：ace_better 6ch 回退传常量 0.5 法线（捕获 predict_dense normal_map） | pytest red（allclose 0.5 断言失败） |
| [x] | TASK-007-03 | TASK-007-02 | `enhanced_ace.py`：常量法线策略（CONSTANT_NORMAL_VALUE=0.5）+ `_estimate_normal_dsine` → `_estimate_gradient_normal`（debug-only，改调用点） | pytest green（TL-007-01） |
| [x] | TASK-007-04 | TASK-007-03 | 失败测试 TL-007-02：模型路由（scene 3ch 优先 / 6ch 回退，两种 input_mode） | pytest red |
| [x] | TASK-007-05 | TASK-007-04 | `resolve_ace_model()` + `build_constant_normal_map()` 接入 `ace_with_better_normal`/`ace_with_normal`（共享 helper） | pytest green（TL-007-02） |
| [x] | TASK-007-06 | TASK-007-05 | 失败测试 TL-007-03：`solve_pnp_with_focal_search` 返回 attempts_summary | pytest red |
| [x] | TASK-007-07 | TASK-007-06 | `pose_utils.py` 带出 attempts_summary（best_partial 跟踪，只增不改既有键） | pytest green（TL-007-03） |
| [x] | TASK-007-08 | TASK-007-07 | 失败测试 TL-007-04：ACE 系失败含 diagnostics 结构 | pytest red |
| [x] | TASK-007-09 | TASK-007-08 | `enhanced_ace.build_ace_failure_diagnostics()` + `ace_better`/`ace_normal` 失败分支组装（含 LAS bbox 重叠率） | pytest green（TL-007-04） |
| [x] | TASK-007-10 | TASK-007-09 | 失败测试 TL-007-05：低点分支无 NameError（死代码） | pytest red（NameError） |
| [x] | TASK-007-11 | TASK-007-10 | 移除 `result` 未定义引用（ace_localizer `ace_with_normal:87`/`ace_rgb_only:199`，低点分支优雅失败） | pytest green（TL-007-05） |
| [x] | TASK-007-12 | TASK-007-11 | 前端镜像失败测试 TL-007-06：失败诊断行统计（tests/test_frontend_ace_better_fix.py 6 用例） | pytest red（NotImplementedError） |
| [x] | TASK-007-13 | TASK-007-12 | `web/app_v10.js`：`localizeFailureDiagLine`（@674）+ 失败渲染接入两处（@1001/1188） | pytest green（TL-007-06）+ node --check 通过 |
| [x] | TASK-007-14 | TASK-007-13 | 回归 TL-007-07（SALAD/ace_rgb_only）+ 门禁 TL-007-08 | 全量 pytest + 三脚本绿 |
| [ ] | TASK-007-15 | TASK-007-14 | 同步任务/清单/测试状态 + commit + push（PR 处置沿用 006 阻塞约定，待用户授权） | git log / status |