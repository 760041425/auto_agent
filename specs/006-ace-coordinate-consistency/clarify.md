# 006 澄清记录

| CL-ID | 问题 | 结论 | 确认方式 | 影响范围 |
| --- | --- | --- | --- | --- |
| CL-006-01 | train_ace 显示「内点 0 | 3D点数 0」是真实值吗？ | 假值。`_run_train_ace` 后台用 `ace_rgb_only()` raw dict 覆盖 `result_json`，未走 `normalize_localization_result`，缺 `inliers`/`total_3d_points` 字段，前端取 undefined 渲染为 0。ACE 真实内点保存在 `quality.inlier_count`。 | 代码证据（registry.py 234-291、contracts.py 26-135） | 后端覆写路径、前端诊断行 |
| CL-006-02 | 「✓ 可信」徽章从哪来？ | `localizeStatusBadge` 在 `coordinate_transform` 缺失时 fallback `result.reliable`（ACE = LAS 验证率>0.3），是**旧判据**；与「坐标差最终判定」卡片（只看 consistency）矛盾。 | 代码证据（app_v10.js 615-628） | 前端徽章逻辑 |
| CL-006-03 | 修复取向「ACE 系接入坐标差判据」与「明确降级为无法判定」二选一，选哪个？ | **降级为主**：ACE 系（含 train_ace）本质是实验/快速算法，接入 H+NPY+consistency 三件套需为其重复 PnP/H 基建，成本高；AC-003-14 规定「无可用坐标差一律不可信」，降级语义天然一致。真正接入列为后续增强（RISK-006-01）。 | 用户授予二选一（本轮消息「①ACE 系接入…或明确降级…」），工程权衡+成本 | 本规格范围 |
| CL-006-04 | 去掉 `result.reliable` fallback 后，历史任务（无 coordinate_transform）徽章全变「无法判定」是否可接受？ | 可接受，且更符合 AC-003-14（旧 LAS 判据不得产生"可信"徽章）。属预期语义收紧，在文档与前端文案中声明。 | 规格语义 | 历史展示 |
| CL-006-05 | 其他非 ACE 算法（flann、pointcloud_descriptor、render_compare…）同样缺 coordinate_transform？ | 是，扩散排查命中同一模式（见 RISK-006-05）。本规格只对齐 ACE 系 + 前端徽章统一逻辑；徽章统一后这些算法自然归入「无法判定」展示，属预期，不扩大战场到各自算法实现。 | 扩散排查 | 前端统一逻辑覆盖即可 |