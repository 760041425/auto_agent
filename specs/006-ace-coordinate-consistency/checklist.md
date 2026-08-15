# 006 完成清单

- [x] 后端：无坐标产物的结果 `coordinate_transform={status:not_available, reason:...}`，`consistency.status=not_available`（AC-006-02）
- [x] 后端：train_ace 完成后 `result_json` 与 `_append_result` 结构等价，含 `inliers`/`total_3d_points`/`timings.total_s`/`coordinate_transform`（AC-006-01）
- [x] 前端：`localizeStatusBadge` 取消 `result.reliable` fallback，缺判据 → 「⚠ 无法判定」（AC-006-03）
- [x] 前端：徽章与判定卡永不矛盾，判定卡 not_available/passed=false 时徽章必非 ✓（AC-006-04）
- [x] 前端：辅助诊断行显示真实 inlier_count / total_3d_points 或 "—"，无假 0（AC-006-05）
- [x] 前端：判定卡对 not_available+reason 显示具体原因文案（AC-006-06）
- [x] 回归：SALAD 系 consistency available+passed 徽章仍 ✓、卡片显示中位差（AC-006-07）
- [x] 门禁：validate-specs / run-all-tests fast / drift-check 全绿（AC-006-08）
- [ ] 浏览器真验：ACE 结果卡不出现 ✓+低可信矛盾，内点/3D 点数非假 0（人工）