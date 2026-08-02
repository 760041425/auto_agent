# BUG-003-05 — 精化步骤固定使用 LightGlue，与初始定位算法不一致

日期：2026-08-02
状态：**待修复**

## 1. 期望与实际

- 期望：精化步骤 (`refine_pose_with_roma`) 应复用初始定位的匹配器（SALAD+RoMa 用 TinyRoMa、LoFTR 路径用 LoFTR、Hybrid 路径用 Hybrid），保持算法一致，避免域差距导致的匹配失败。
- 实际：`refine_pose_with_roma` 硬编码调用 `_lightglue_match`（LightGlue），与初始定位使用的匹配器无关。函数名带 "roma" 但实际跑 LightGlue，命名与行为双重误导。

## 2. 复现矩阵

| 环境/版本 | 输入与路径 | 预期 | 实际 | 证据 |
| --- | --- | --- | --- | --- |
| task #249 `/localize/refine` | SALAD+RoMa 初始定位 (TinyRoMa)，坐标差 5.484m | 精化复用 TinyRoMa 或至少能匹配足够点 | LightGlue 只匹配 1 对，报 "LightGlue matches too few: 1" 退出 | 后端日志 2026-08-02 23:23:05 |
| `refine_pose_with_roma` 源码 | 任意输入 | 可配置匹配器 | 硬编码 `_lightglue_match(q_small, ref_img, sample_num=3000)` | `salad_roma.py:1739` |

## 3. 根因分析

### 3.1 5 Why

1. 为什么 task #249 精化失败？LightGlue 在查询图与重投影图之间只找到 1 对匹配（阈值要求 ≥10）。
2. 为什么只匹配 1 对？初始位姿坐标差 5.484m，重投影图与原图视角/外观差异大，LightGlue (DISK+LG) 对这种大差异鲁棒性不足。
3. 为什么用 LightGlue 而不是 TinyRoMa？`refine_pose_with_roma` 硬编码调用 `_lightglue_match`，写死在第 1739 行。
4. 为什么写死？该函数是早期为 SALAD+RoMa 路径写的精化工具，没有抽象匹配器接口；后续扩展出 v2/LoFTR/Hybrid 多条路径后，精化函数没有同步抽象化。
5. 为什么测试未发现？`TL-003-32` 只覆盖初始定位的 TinyRoMa 调用，没有测试精化路径；`/localize/refine` 是独立 API 端点，没有自动化契约测试。

### 3.2 为什么未被测试/监控发现

- `refine_pose_with_roma` 是 API 端点 (`api/routes/localize.py:413`) 调用的独立函数，不在 runner 主流程中，快速测试不覆盖。
- 精化失败只返回 `{"success": False, "error": "..."}`，不抛异常，静默降级。
- 历史 task 坐标差较小时 LightGlue 偶尔能成功精化，掩盖了算法不一致问题。

## 4. 影响面

- **直接影响**：所有通过 `/localize/refine` 端点做后处理的 SALAD+RoMa 任务 — 精化阶段无法复用 TinyRoMa，位姿偏差大时几乎必然失败。
- **间接影响**：其他算法（v2/LoFTR/Hybrid）如果将来接入同一精化端点，也会被强制走 LightGlue，与各自初始匹配器不一致。
- **不影响**：初始定位流程（PnP 求解、Z 维度一致性检查、artifact 生成）— 这些都不经过 `refine_pose_with_roma`。

## 5. 修复方案

1. `refine_pose_with_roma` 增加 `matcher_type: str` 参数（`"tiny_roma" | "lightglue" | "loftr" | "hybrid"`），默认 `"lightglue"` 保持向后兼容。
2. 根据 `matcher_type` 分派到 `_roma_match` / `_lightglue_match` / LoFTR / Hybrid 匹配器。
3. API 端点从初始结果的 `algorithm_id` / `tag` 推导 `matcher_type`，传入精化函数。
4. 重命名函数为 `refine_pose`（去掉误导性的 "roma"），或保留别名但修正 docstring。

## 6. 扩散覆盖矩阵

| 同模式位置 | 是否受影响 | 处理 | 测试 |
| --- | --- | --- | --- |
| `refine_pose_with_roma` | 是 | 增加 matcher_type 分派 | 参数化匹配器测试 |
| `api/routes/localize.py` refine 端点 | 是 | 从 algorithm_id 推导 matcher_type | API 契约测试 |
| `_roma_match` (TinyRoMa) | 否 | 已有实现，直接复用 | — |
| `_lightglue_match` | 否 | 已有实现，作为默认回退 | — |
| v2/LoFTR/Hybrid 匹配器 | 否 | 已有 `_match_tile_with_loftr` 等，可适配 | — |

## 7. 回归测试

- `test_refine_uses_initial_matcher`（新增）：给定 SALAD+RoMa 初始结果，验证精化调用 TinyRoMa 而非 LightGlue。
- `test_refine_falls_back_to_lightglue_by_default`（新增）：无 matcher_type 参数时默认 LightGlue，向后兼容。
- `test_refine_rejects_unknown_matcher_type`（新增）：未知 matcher_type 返回结构化错误。
- 端到端：task #249 同输入重跑 `/localize/refine`，验证精化成功（TinyRoMa 对重投影图匹配 ≥10 对）。

## 8. 风险与回滚

- 风险：TinyRoMa 在重投影图上的匹配质量未验证（训练域是 tile↔query，不是 reproj↔query）；可能需要调整 `sample_num`。
- 风险：函数签名变更影响现有调用方（仅 `api/routes/localize.py:413` 一处）。
- 回滚：恢复硬编码 LightGlue 版本即可；不涉及数据库 schema。

## 9. Before/After

- Before：task #249 精化 → `LightGlue matches too few: 1` → 失败退出。
- After：task #249 精化 → 复用 TinyRoMa 匹配 → 成功精化位姿（或至少匹配数 ≥10 进入 PnP）。

## 10. Changelog

- 2026-08-02：确认缺陷，task #249 日志证据，完成 5 Why；标记为待修复。
