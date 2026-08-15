# 005 实施计划

## 步骤

1. **Red**: 写 `tests/test_frontend_coordinate_display.py`，包含 5 个测试场景
2. **Red**: 跑测试，确认全部失败
3. **Green**: 修改 `web/app_v10.js` 的 `verifyCoordinatePoint` 函数
4. **Green**: 跑测试，确认全部通过
5. **门禁**: 跑 `validate-specs.sh` + `run-all-tests.sh fast` + `drift-check.sh`
6. **DDD**: 更新 `contexts/spatial-localization/README.md` 术语
7. **commit + push**

## 预期产物

- `specs/005-coordinate-transform-fix/` 八件套
- `tests/test_frontend_coordinate_display.py`（新建）
- `web/app_v10.js`（修改）
