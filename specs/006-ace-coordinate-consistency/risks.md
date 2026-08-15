# 006 风险与对策

| RISK-ID | 风险 | 等级 | 缓解 |
| --- | --- | --- | --- |
| RISK-006-01 | ACE 系（train_ace/ace_rgb/ace_normal/ace_better）仍无真正坐标差判据产物，降级为「无法判定」后无法给出坐标差数值，用户仍得不到绝对可信结论 | 中 | 明确为后续增强：若需 ACE 定位结果拥有可信状态，须新建规格为其接入 H+最终位姿 XYZ NPY+consistency 三件套（复用 `build_local_coordinate_transform_context`）；本规格保证展示语义不再误导 |
| RISK-006-02 | 修改徽章去 `result.reliable` fallback 后，历史任务（无 coordinate_transform）徽章从「✓可信」变为「⚠无法判定」，观察行为变化可能被误报为回归 | 中 | 属 AC-003-14 预期语义收紧；在 clarifying 记录 + 前端文案明示「最终可信状态只由坐标差决定」；回归测试 TL-006-07 覆盖 SALAD 系不变 |
| RISK-006-03 | `_run_train_ace` 后台线程与 `run_localize_task` 主循环并发写 `task.result_json`，重构归一化覆写时引入竞态或字段丢失 | 中 | 保持单线程覆写（后台完成时一次性替换）；归一化幂等；集成测试 TL-006-06 验证完成后终态 |
| RISK-006-04 | 前端 JS 无自动化运行时，镜像等价函数可能与真实 JS 行为漂移 | 低 | 沿用 specs/005 先例（Python 镜像）；完成时浏览器人工真验 AC-006-03~06 + 附截图；镜像函数与 JS 函数保持同名同注释对 |
| RISK-006-05 | 其他非 ACE 算法（flann、pointcloud_descriptor、render_compare、depth_icp 等）同样缺 coordinate_transform，徽章统一后其展示归入「无法判定」 | 低 | 属预期语义；本规格只统一前端判据逻辑，不逐算法实现 H 产物（避免扩战场）；记录于 clarify CL-006-05，后续按需建规格 |
| RISK-006-06 | `normalize_localization_result` 的 `coordinate_transform` fallback 输出结构被其他调用方依赖（005 接口、单点查询） | 低 | 只**新增** `consistency`/`reason` 子字段，不删除/不改变 `status` 语义；跑全量测试确认无回归 |