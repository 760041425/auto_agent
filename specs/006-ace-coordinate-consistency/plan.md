# 006 实施计划

## 批次划分（单流水线，顺序执行）

### 批次 P0：后端契约归一化（TL-006-01）
1. 写失败测试：`services/tests/` 新增归一化契约测试——`normalize_localization_result` 对带 `pose/quality/validations.las_nearest` 的 ACE raw dict（无 `coordinate_transform`）→ `coordinate_transform.status == "not_available"`、`consistency.status == "not_available"`、`inliers == quality.inlier_count`、`total_3d_points == match_count`。
2. 跑 → 确认红（当前 `verify` 不产生 `consistency` 结构）。
3. Green：仅扩展 `contracts.py` 的 `coordinate_transform` fallback 结构，加入 `consistency: {status: "not_available", reason: ...}` 与明确 `reason`；不触碰其他已有结构。4. 跑 → 绿。

### 批次 P1：train_ace 覆写归一化对齐（TL-006-02）

1. 抽取共享 helper（建议函数 `hydrate_train_ace_result(result, algorithm_id, ...)` 或复用 `contracts.normalize_localization_result` + 公共路径处理）供 `registry._run_train_ace` 与 `_append_result` 共用。
2. 修改 `_run_train_ace` 后台完成分支：`result_json = {"results": [normalized], "total": 1}`（保留 `tag`、`success`）。
3. 测试用注入的假 trainer（禁止真实训练）断言覆写后结构与 `_append_result` 等价。

### 批次 P2：前端镜像测试与实现（TL-3/4/5）

5. `tests/` 新增前端镜像函数（沿用 005 先例：Python 等价函数而非 Node runtime）：
   - `_resolve_localize_badge(result)`（AC-006-03/04）
   - `_render_coordinate_decision(result)` 含 reason 文案映射（AC-006-06）
   - `_diagnostic_detail_line(result)`（AC-006-05）
6. 先写失败测试 → 红 → 同步修改 `web/app_v10.js` 对应函数 → 绿。

### 批次 P3：集成（TL-006-06）+ 文档 + 门禁

7. `api/tests` 增加 mock 训练完成的集成测试：断言 `GET /localize/{task_id}` results[0] 含 `coordinate_transform.not_available` 且不出现旧字段缺失。
8. 同步 `docs/ubiquitous-language.md`、`docs/context-map.md`、`contexts/spatial-localization/README.md`。
9. 全量门禁：`validate-specs.sh` + `run-all-tests.sh fast` + `drift-check.sh`。
10. 浏览器真验（人工）：ACE 结果卡不出现 ✓+低可信矛盾；徽章为「⚠ 无法判定」；内点/3D 点数非假 0。

## 验证方法

- Red→Green：每次改动用 `pytest` 单测验证（红输出截图/文本留证据）。
- 前端无 JS 测试运行时：沿用 005 的 Python 镜像等价验证 + 浏览器人工真验 AC-006-xx。
- 禁止真实 ACE 训练（13 分钟/次），全部测试用注入/ mock。