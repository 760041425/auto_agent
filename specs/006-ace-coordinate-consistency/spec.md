# 006 ACE 定位结果坐标差最终判定链路对齐

状态：待实施
上下文：空间定位、坐标交叉验证、可信判定

## 背景与目标

### 背景

AC-003-14（specs/003）规定：**最终可信只由「本地 H→SLAM 与最终位姿 NPY 的多点中位坐标差」决定（严格 `<0.3m`）**；内点数、相似度、LAS 验证率仅作辅助诊断，不得单独产生"可信"状态；没有可用坐标差时一律低可信。

实际运行时发现 `train_ace`（L. 训练ACE+定位）等 ACE 系算法返回的结果与这一判据体系**脱节**，具体证据（代码 + `logs/backend.log` 2026-08-09 21:15-21:28 实况）：

1. **缺坐标差判据产物**：`services/localizer/registry.py` 的 `_run_train_ace`（经过 `enhanced_ace.train_ace_on_scene` → `ace_localizer.ace_rgb_only`）返回字典只有 `pose/quality/validations.las_nearest/elapsed`，**没有 `coordinate_transform`（H 单应 + 最终位姿 XYZ NPY），也没有 `consistency`**。
2. **判据从未被调用**：`build_local_coordinate_transform_context` + `evaluate_local_coordinate_consistency`（`verify_projection.py`）全仓库只在 SALAD 系被调用（`salad_roma.py:1674`、`salad_roma_v2.py:927`）；ACE 系（`ace_localizer` / `enhanced_ace` / `registry._run_train_ace`）一律不调用。日志中 `evaluate_consistency 被调用` 只出现在 SALAD 系，train_ace 任务全程无此日志。
3. **后台覆写绕过契约归一化**：`run_localize_task`（`api/routes/localize.py:246`）对普通算法用 `_append_result`（内部 `normalize_localization_result`）（`contracts.py`）归一化，但 `_run_train_ace` 训练完成后用 `ace_rgb_only()` 的 **raw dict 直接覆盖** `task.result_json`，导致 `inliers/total_3d_points/coordinate_transform` 字段缺失（前端显示假 0/缺失）。
4. **前端徽章与最终判定矛盾**：`web/app_v10.js` `localizeStatusBadge()`（615-628 行）在 `coordinate_transform` 缺失时 fallback 到旧的 `result.reliable`（`ace_rgb_only` 里 = LAS 验证率 > 0.3），于是同屏出现「✓ 可信」徽章 + 「坐标差最终判定：未生成可用的多点坐标差，低可信」卡片。

### 目标

让 ACE 系（`train_ace`、`ace`、`ace_rgb`、`ace_normal`、`ace_better`）结果的**可信展示与 AC-003-14 判据语义对齐**：

1. 契约归一化对齐：后台覆写路径与前台 `_append_result` 的输出格式一致（`inliers`、`total_3d_points`、`timings`、`coordinate_transform` 齐备）。
2. 明确"无法判定"的独立状态：无坐标差判据时统一输出明确 reason，不再被混淆成"可信"或裸"低可信"。
3. 徽章与判定解耦：`✓ 可信` 只能由坐标差判据产生，缺判据时徽章显示「⚠ 无法判定」；内点/相似度/LAS 不再作为徽章依据。
4. 前端字段补齐：辅助诊断行不再因缺字段显示假 `0`。

> 决策取向：**明确降级为「无法判定」独立状态**（低成本、与 AC-003-14 兜底语义一致）为主修复；「ACE 系真正接入坐标差判据（生成 H + NPY 产物）」列为后续增强，见 D-006-01 / RISK-006-01。

## 范围

### In Scope

- 后端契约：`services/localizer/contracts.py` 对无 `coordinate_transform` 的结果输出明确 `not_available` + `consistency.status=not_available` + 明确 `reason`。
- 后端流程：`services/localizer/registry.py` `_run_train_ace` 完成后覆写 `result_json` 统一走 `normalize_localization_result`（抽取共享 helper，与 `_append_result` 对齐）。
- 后端测试：`services/tests/` 或 `api/tests/` 新增契约/集成测试（训练必须 mock，禁止真实 13 分钟训练）。
- 前端：`web/app_v10.js` 的 `localizeStatusBadge`、`renderCoordinateReliabilityDecision`、辅助诊断行（`inliers`/`total_3d_points` 展示）。
- 前端测试：`tests/test_frontend_*` 镜像等价函数（沿用 specs/005 先例）。
- 文档：`docs/ubiquitous-language.md`（坐标差最终判定表述）、`docs/context-map.md`、`contexts/spatial-localization/`。

### Out of Scope

- 不修改 AC-003-14 判定语义（门限 `<0.3m`、无坐标差即不可信）。
- 不在此包内实现 ACE 系真正生成坐标差判据产物（H 拟合/PnP/NPY 三件套）——记录 RISK-006-01，后续规格推进。
- 不改 SALAD 系与单点会话坐标交叉查询接口、不改 API 返回的既有稳定字段。

## 验收标准

- **AC-006-01**：`train_ace`（或任一 ACE 系 runner）完成后的 `task.result_json` 与 `_append_result` 产物结构等价——含 `inliers`（来自 `quality.inlier_count`）、`total_3d_points`、`timings.total_s`、`coordinate_transform`。
- **AC-006-02**：归一化后无坐标产物时，`result.coordinate_transform = {status: "not_available", reason: 明确原因}` 且 `consistency.status == "not_available"`。
- **AC-006-03**：前端徽章逻辑 `localizeStatusBadge` 取消 `result.reliable` fallback：判定 `coordinate_transform.status === "ready" && consistency.status === "available" && passed → ✓ 可信`；`coordinate_transform` 缺失或 `status !== "ready"` → `⚠ 无法判定`；`ready 但 consistency` 未 available → `⚠ 无法判定`；绝不因 `result.reliable` 显示 ✓。
- **AC-006-04**：徽章与「坐标差最终判定」卡片永不矛盾：卡片 `consistency.status==="available" && passed=false` 或 `not_available` 时，徽章必不是 ✓。
- **AC-006-05**：辅助诊断行显示真实内点/3D 点数（`quality.inlier_count` / `total_3d_points` 或 "—"），不再把缺失字段渲染成假 `0`。
- **AC-006-06**：判定卡片在 `not_available + reason` 时展示「未生成可用的多点坐标差」+ 具体 cause 的说明文案（前端对该 reason 做文案映射）。
- **AC-006-07**：回归——SALAD 系 `consistency available + passed` 时徽章仍为 ✓ 可信、卡片显示中位差（行为不变）。
- **AC-006-08**：`./scripts/validate-specs.sh`、`./scripts/run-all-tests.sh fast`、`./scripts/drift-check.sh`、以及 pytest 全部绿。

## 成功标准

- 一个 ACE 系定位结果不再出现「✓ 可信」+「低可信」同屏矛盾。
- ACE 系无坐标判据时页面明确显示「⚠ 无法判定」+ 原因，内点/3D 点数真实或 "—"。
- SALAD 系行为完全不变；无回归。
- 全量门禁绿。

## 关联

- `specs/003-multi-algo-verification/`（AC-003-14 唯一判据；`bugfix-local-coordinate-transform.md`：无坐标差一律低可信）
- `specs/004-plane-awake-homography/`（H→SLAM 只对地面点准确）
- `specs/005-coordinate-transform-fix/`（前端镜像测试先例）
- `services/localizer/registry.py`、`contracts.py`、`verify_projection.py`、`ace_localizer.py`、`enhanced_ace.py`
- `api/routes/localize.py`（`_append_result` 归一化入口）
- `web/app_v10.js`（徽章 615-628、判定卡 630-648、诊断行 1088、二次加载 902）