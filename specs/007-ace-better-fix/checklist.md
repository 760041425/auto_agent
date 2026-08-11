# 007 完成清单

- [x] 后端：`ace_better`/`ace_normal` 不再用梯度伪法线喂 6ch 模型；输入与训练分布对齐（AC-007-01）——6ch 回退走常量 0.5 占位，3ch 路径走 RGB-only
- [x] 后端：模型路由 scene 3ch 优先、6ch+常量占位回退，`input_mode`/`normal_source` 标注（AC-007-02）——`resolve_ace_model()`
- [x] 后端：`solve_pnp_with_focal_search` 返回 `attempts_summary`（成功与失败分支，只增不改）（AC-007-03）
- [x] 后端：ACE 系失败返回 `diagnostics`（pnp / pred_xyz / las_bbox / overlap / model / input_mode）（AC-007-04）——`build_ace_failure_diagnostics()`
- [x] 后端：`ace_with_normal`/`ace_rgb_only` 低点分支无 NameError（死代码 `result` 引用已移除）（AC-007-05）
- [x] 前端：失败诊断行渲染 PnP 统计 + 预测 Z 范围，缺字段 "—"（AC-007-06）——`localizeFailureDiagLine` 镜像 `_localize_failure_diag_line`
- [x] 回归：`ace_rgb_only` 成功路径与 SALAD 行为不变（AC-007-07）
- [x] 门禁：validate-specs（7 包）/ run-all-tests fast（112 passed）/ drift-check（0 错误）/ 全量 pytest（134 passed，1 条既有 @integration 数据缺失）全绿（AC-007-08）
- [x] 文档：领域术语无变化，`docs/` 与 `contexts/` 无需同步（drift-check 0 错误佐证）
- [ ] 浏览器人工真验（可选）：真实查询图下 ace_better 的 PnP 结果与 diagnostics 分布（含 scene 3ch 模型存在时是否走 ace_scene_rgb3ch、失败时诊断行显示统计）