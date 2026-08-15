# 007 测试清单

| 状态 | TL-ID | 映射 AC | 层级 | 场景与期望 |
| --- | --- | --- | --- | --- |
| [x] | **TL-007-01** | AC-007-01 | 单元/注入 | `ace_with_better_normal`（mock 模型加载 + 注入假 `predict_dense` 捕获 `normal_map`）：6ch 回退路径传入**常量 0.5 占位**（`np.allclose(nm, 0.5)`），非梯度噪声；结果 `input_mode=="ace_6ch_constant_normal"`、`normal_source=="constant_fallback"`（test_ace_better_normal_6ch_fallback_passes_constant_normal） |
| [x] | **TL-007-02** | AC-007-02 | 单元/路由 | `resolve_ace_model()`：(a) mock `ace_model_scene.pth` 存在且 3ch → 返回 scene 路径、`predict_dense` 不传 normal_map、`input_mode=="ace_scene_rgb3ch"`；(b) mock 不存在 → 回退默认 6ch + 常量占位（test_resolve_ace_model_route_prefers_scene3ch / _route_fallback_6ch_constant） |
| [x] | **TL-007-03** | AC-007-03 | 单元/PnP | `solve_pnp_with_focal_search` 无内点场景失败返回含 `attempts_summary`（`tried_candidates>=1`、`best_inliers>=0`、`best_reproj_error_px` 数值）；成功场景同样带出（test_solve_pnp_focal_search_failure/success_returns_attempts_summary） |
| [x] | **TL-007-04** | AC-007-04 | 单元/集成 | mock PnP 失败 → `ace_better` 结果含 `diagnostics.{pnp, pred_xyz(z_min/z_max/center/count), las_bbox, overlap_with_las_bbox, model(path/in_channels), input_mode}`；overlap 为 [0,1] 比值（test_ace_better_normal_pnp_failure_includes_diagnostics） |
| [x] | **TL-007-05** | AC-007-05 | 单元/回归 | `ace_with_normal` 预测点不足/低点分支调用不抛 NameError，返回 `success=False` + `error`（引用 `result` 的死代码已移除）（test_ace_with_normal_low_point_branch_no_nameerror） |
| [x] | **TL-007-06** | AC-007-06 | 前端等价 | `_localize_failure_diag_line(result)`：含 `diagnostics.pnp.best_inliers=...`、`best_reproj_error_px=...`、`pred_xyz` Z 范围；字段缺失渲染 "—" 而非 0/None；无 diagnostics 兜底原文案不崩溃（tests/test_frontend_ace_better_fix.py 6 用例） |
| [x] | **TL-007-07** | AC-007-07 | 回归 | `ace_rgb_only` 成功路径行为不变（死代码移除仅影响低点分支）；SALAD 系回归测试全绿（services/tests 全量 + run-all fast 112 passed） |
| [x] | **TL-007-08** | AC-007-08 | 门禁 | `validate-specs.sh`（7 包）+ `run-all-tests.sh fast`（112 passed）+ `drift-check.sh`（0 错误，5 条历史警告）+ 新旧 pytest（134 passed，1 条既有 @integration 数据缺失失败）全绿 |

## TDD 顺序（单流水线，禁止一次写一堆）

### 批次 P0：法线策略 + 模型路由
1. TL-007-01 → 红 → 绿（enhanced_ace 常量法线 + 更名 _estimate_gradient_normal）
2. TL-007-02 → 红 → 绿（resolve_ace_model + 两 runner 接入）

### 批次 P1：PnP 诊断 + 死代码
3. TL-007-03 → 红 → 绿（pose_utils attempts_summary）
4. TL-007-04 → 红 → 绿（diagnostics 组装）
5. TL-007-05 → 红 → 绿（死代码移除）

### 批次 P2：前端镜像
6. TL-007-06 → 红 → 绿（失败诊断行）

### 批次 P3：回归 + 门禁
7. TL-007-07 → 回归绿
8. TL-007-08 → 全量门禁

> 注意：所有训练路径与真实模型推理必须被 mock/注入；禁止真实 ACE 训练（约 13 分钟/次）与真实大图 PnP。