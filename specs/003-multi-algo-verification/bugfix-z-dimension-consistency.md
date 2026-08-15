# BUG-003-04 — 一致性检查忽略 Z 维度，高度异常无法被可靠判据捕获

日期：2026-08-02
状态：已修复

## 1. 期望与实际

- 期望：坐标差最终判据应反映查询像素经 H→SLAM 到 NPY 的真实三维偏差。单应矩阵 H 把像素映射到 SLAM 地面平面（Z=0），NPY 包含真实高度，二者应比较三维欧氏距离。
- 实际：`evaluate_local_coordinate_consistency` 只比较 XY 平面距离，显式丢弃 Z 分量。当位姿在 XY 方向拟合良好但高度（Z）严重偏离时，中位差仍可能低于门槛，错误地把不可靠位姿判为 `reliable=true`。

## 2. 复现矩阵

| 环境/版本 | 输入与路径 | 预期 | 实际 | 证据 |
| --- | --- | --- | --- | --- |
| `evaluate_local_coordinate_consistency` | XY 完全对齐、Z 偏移 2.0m 的 NPY | median_m = 2.0m（Z 被计入） | median_m = 0.0m（Z 被忽略） | 单元测试 |
| `query_local_coordinate_transform` | 同点位单点查询 | difference_m = 2.0m（已含 Z） | difference_m = 2.0m | 代码已正确 |
| 真实定位任务 | XY 拟合良好但高度错误的位姿 | `reliable=false` | `reliable=true`（假阳性） | 逻辑推导 |

## 3. 根因分析

### 3.1 5 Why

1. 为什么高度错误不会被判据捕获？`evaluate_local_coordinate_consistency` 只取 NPY 的前两维 (`:2`) 与 H→SLAM 的 XY 比较，Z 不参与距离计算。
2. 为什么只取 XY？早期实现把 H 当作纯平面映射，文档注释写"只比较 XY 平面距离，忽略 Z 分量"，没有意识到 NPY 自带高度信息、且 `query_local_coordinate_transform` 单点路径已经按三维差计算。
3. 为什么单点路径与多点判据不一致？`query_local_coordinate_transform` 使用 `slam_z=0.0` 构造三维坐标再求欧氏距离（`verify_projection.py:408-416`），多点判据却没有复用同一逻辑。
4. 为什么测试未发现？`TL-003-28` 的测试数据把 Z 固定为 5.0m 而 XY 只偏移 1.0m，断言 `median_m == 1.0`，等于在验证"忽略 Z"这个错误行为。
5. 为什么规格没有阻止？`AC-003-14` 只规定"多点三维差"作为判据，但实现与措辞不一致，规格校验不覆盖数值语义。

### 3.2 为什么未被测试/监控发现

- `test_coordinate_consistency_median_is_the_final_pass_fail_standard` 的断言 (`median_m == 1.0`) 与 Z=5.0 测试数据绑定，实际上在强化错误实现。
- 真实样本（task #225/#226）的高度偏差与 XY 偏差耦合出现，无法单独暴露 Z 缺失。
- `query_local_coordinate_transform` 与多点判据使用不同代码路径，单点查询正确掩盖了多点判据的缺陷。

## 4. 影响面

- 直接影响 V2 与 SALAD+RoMa（原版）的 `reliable` 最终判据：所有依赖 `evaluate_local_coordinate_consistency` 的可信判定都可能出现假阳性（高度错误但判为可靠）。
- 不影响 2D 单应拟合诊断、LAS 邻近性验证、独立真值 Benchmark 路径。
- 不影响 `query_local_coordinate_transform`（单点查询已正确计算三维差）。

## 5. 修复方案

1. `evaluate_local_coordinate_consistency` 改为比较三维欧氏距离：slam_xyz = (slam_x, slam_y, 0)，npy_xyz = NPY XYZ。
2. `decision_metric` 从 `median_xy_plane_difference_m` 改为 `median_3d_difference_m`，与 `query_local_coordinate_transform` 的单点差值语义一致。
3. 更新文档注释，明确 H→SLAM Z=0、NPY 含真实高度、比较三维距离。
4. 更新 `TL-003-28` 测试数据（Z=0 贴地）并新增 Z 维度专项测试。

## 6. 扩散覆盖矩阵

| 同模式位置 | 是否受影响 | 处理 | 测试 |
| --- | --- | --- | --- |
| `evaluate_local_coordinate_consistency` | 是 | 改为三维距离 | TL-003-28 + 新增 Z 维度测试 |
| `query_local_coordinate_transform` | 否 | 已是三维差 | 既有测试保持 |
| `build_local_coordinate_transform_context` | 否 | 仅拟合 H、保存 NPY | — |
| `build_projection_xyz_map` | 否 | 仅生成 XYZ 图 | — |
| 前端展示 | 否 | 字段名 `median_m` 不变 | — |

## 7. 回归测试

- `test_coordinate_consistency_median_is_the_final_pass_fail_standard`：更新为 Z=0 贴地数据，断言 `median_m == 1.0`（仅 XY 偏移），`decision_metric == "median_3d_difference_m"`。
- `test_coordinate_consistency_incorporates_z_dimension`（新增）：
  - XY 对齐 + Z 偏移 2.0m → `median_m == 2.0`，证明 Z 被计入。
  - 门槛低于 Z 偏移 → `passed is False`，证明 Z 参与判定。
  - XY 偏移 + Z 偏移合成 → `median_m ≈ sqrt(5) ≈ 2.236`，证明三维欧氏距离。

## 8. 风险与回滚

- 风险：修复后更多位姿会因高度偏差被判为 `reliable=false`，可靠率下降是正确行为（之前是假阳性）。
- 风险：历史 task 的 `median_m` 数值语义变化（从 XY 平面差变为三维差），但历史任务本就标注"非绝对精度"，且数值字段名 (`median_m`) 不变。
- 回滚：恢复 XY 比较逻辑即可；不涉及数据库 schema。

## 9. Before/After

- Before：`evaluate_local_coordinate_consistency` 对 XY 对齐 + Z=2.0m 偏移的 NPY 返回 `median_m = 0.0m, passed = true`（假阳性）。
- After：同输入返回 `median_m = 2.0m`，门槛 1.0m 时 `passed = false`，正确暴露高度异常。
- After：`decision_metric` 从 `median_xy_plane_difference_m` 改为 `median_3d_difference_m`，与单点查询语义一致。

## 10. Changelog

- 2026-08-02：确认 Z 维度缺失缺陷，完成 5 Why；新增 Z 维度测试先 Red 后 Green，既有 `TL-003-28` 测试同步更新。
- 2026-08-02：快速测试 77 passed, 4 deselected；规格校验、漂移检查待门禁确认。
