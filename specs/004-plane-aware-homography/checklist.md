# 004 交付检查清单

## Definition of Ready

- [x] 问题代码路径和当前失败门禁已识别（`verify_projection.py` 行 231-236，全点拟合 H）
- [x] 受影响上下文与依赖方向已明确（空间定位、本地坐标转换）
- [x] P0 修复范围、非目标和回滚边界已明确
- [x] 每条验收标准已映射测试场景（AC-004-01 至 AC-004-08）
- [x] `CL-004-01` 至 `CL-004-08` 按本轮修复授权采用

## Definition of Done

### 代码实现

- [ ] `services/localizer/plane_detection.py` 新建，`segment_plane()` 函数实现
- [ ] `services/localizer/verify_projection.py` 的 `build_local_coordinate_transform_context` 内部行为变更（先分割后拟合）
- [ ] 函数签名向后兼容（新增参数带默认值）
- [ ] `context` 返回字典新增 `plane_segmentation` 字段
- [ ] 地面点不足 4 个时回退到全点拟合

### 测试

- [ ] `services/tests/test_plane_detection.py` 新建，5+ 测试全部通过
- [ ] `services/tests/test_layered_homography.py` 新建，7+ 测试全部通过
- [ ] 现有测试（`test_localization_regressions.py`）全部通过（无回归）
- [ ] 确定性测试：同输入同 seed 多次调用返回相同结果

### 门禁

- [ ] `./scripts/validate-specs.sh` 通过
- [ ] `./scripts/run-all-tests.sh fast` 通过
- [ ] 变更范围 Ruff 检查通过
- [ ] `./scripts/drift-check.sh` 通过

### 验收标准覆盖

- [ ] AC-004-01：`segment_plane` 接口和返回值
- [ ] AC-004-02：纯地面/混合输入分割正确性
- [ ] AC-004-03：`build_local_coordinate_transform_context` 内部行为变更
- [ ] AC-004-04：退化路径可观察
- [ ] AC-004-05：合成数据精度提升
- [ ] AC-004-06：一致性判定不退化
- [ ] AC-004-07：确定性
- [ ] AC-004-08：阈值可配置

### 文档

- [ ] 规格包八件套齐全（spec/clarify/plan/tasks/testlist/decisions/risks/checklist）
- [ ] 稳定 ID 齐全（AC/TL/RISK/TASK/DEC/CL）
