# 006 测试清单

| 状态 | TL-ID | 映射 AC | 层级 | 场景与期望 |
| --- | --- | --- | --- | --- |
| [x] | **TL-006-01** | AC-006-01/02 | 单元/契约 | `normalize_localization_result` 处理 ACE raw（有 pose/quality/validations.las_nearest、无 coordinate_transform）：`normalized["coordinate_transform"]["status"]=="not_available"`、`consistency.status=="not_available"`、`inliers==quality.inlier_count`、`total_3d_points==match_count` |
| [x] | **TL-006-02** | AC-006-01 | 单元/注册表 | `_run_train_ace` 完成分支（mock 训练）写入的 `result_json.results[0]` 与 `_append_result` 结构等价：键集合含 inliers/total_3d_points/coordinate_transform/timings.total_s，success/tag 保留 |
| [x] | **TL-006-03** | AC-006-03/04 | 前端等价 | `_resolve_localize_badge`：consistency available+passed → `✓ 可信`；`coordinate_transform` 缺失 → `⚠ 无法判定`；consistency not_available → `⚠ 无法判定`（即使 `result.reliable===true` 也不得返回 ✓） |
| [x] | **TL-006-04** | AC-006-06 | 前端等价 | `_render_coordinate_decision`：not_available + reason 时输出含原因映射文案（如「未生成可用的多点坐标差」+ cause 说明），且不含「✓」徽章字符串 |
| [x] | **TL-006-05** | AC-006-05 | 前端等价 | `_diagnostic_detail_line`：quality.inlier_count=8、total_3d_points=8 时输出「内点 8 \| 3D点数 8」；字段缺失时输出 "—" 而非 0 |
| [x] | **TL-006-06** | AC-006-01/03 | 集成/API | mock 训练完成的 task 经 `GET /localize/{task_id}` 返回 results[0] 含 `coordinate_transform.status == "not_available"`、`inliers` 非缺省；前端对其渲染徽章为无法判定 |
| [x] | **TL-006-07** | AC-006-07 | 回归 | SALAD 系 consistency available+passed 的 result：徽章 `✓ 可信`、判定卡显示中位差（不回归） |
| [x] | **TL-006-08** | AC-006-08 | 门禁 | `validate-specs.sh` + `run-all-tests.sh fast` + `drift-check.sh` + 新老测试全绿 |

## TDD 顺序（单流水线，禁止一次写一堆）

### 批次 P0：后端契约
1. 写 TL-006-01 → 跑 → 确认红 → 实现 contracts → 确认绿
2. 写 TL-006-02 → 跑 → 确认红 → 实现 registry → 确认绿

### 批次 P1：前端等价
3. 写 TL-006-03 → 跑 → 确认红 → 实现 badge → 确认绿
4. 写 TL-006-04 → 跑 → 确认红 → 实现判定卡文案 → 确认绿
5. 写 TL-006-05 → 跑 → 确认红 → 实现诊断行 → 确认绿

### 批次 P2：集成 + 回归 + 门禁
6. 写 TL-006-06（mock 训练）→ 红 → 绿
7. 写 TL-006-07 回归 → 绿（确保 SALAD 不回归）
8. 全量门禁 TL-006-08

> 注意：所有训练路径必须被 mock 或注入假实现，禁止真实 ACE 训练（约 13 分钟/次）。