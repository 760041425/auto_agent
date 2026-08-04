# 004 实施任务

Phase A 已实施（TASK-004-01 至 TASK-004-07 全部完成）。Phase C（诊断量化）待实施。

| 状态 | TASK-ID | 依赖 | 任务 | 完成证据 |
| --- | --- | --- | --- | --- |
| [x] | TASK-004-01 | 无 | 新建 `services/localizer/plane_detection.py`，实现 `segment_plane()` 函数骨架（含参数校验、返回 `(None, None)` 占位） | 文件存在，函数可导入，返回 (None, None) |
| [x] | TASK-004-02 | TASK-004-01 | 新建 `services/tests/test_plane_detection.py`，写 5 个失败测试让它红 | `pytest services/tests/test_plane_detection.py` 红（ImportError） |
| [x] | TASK-004-03 | TASK-004-02 | 实现 `segment_plane()` 让它绿（RANSAC + 精化） | `pytest services/tests/test_plane_detection.py` 绿（7/7 passed） |
| [x] | TASK-004-04 | TASK-004-03 | 修改 `verify_projection.py` 的 `build_local_coordinate_transform_context`：新增 `plane_distance_threshold`、`plane_seed` 参数（带默认值）；内部先调用 `segment_plane` 过滤；地面内点 ≥4 时只用地面点拟合 H；地面内点 <4 时回退到全点拟合；返回 `context["plane_segmentation"]` 字段 | 现有测试仍通过（向后兼容），新行为可通过新测试验证 |
| [x] | TASK-004-05 | TASK-004-04 | 新建 `services/tests/test_layered_homography.py`，写 5 个失败测试让它红 | `pytest services/tests/test_layered_homography.py` 红（TypeError） |
| [x] | TASK-004-06 | TASK-004-05 | 让分层 H 测试绿 | `pytest services/tests/test_layered_homography.py` 绿（7/7 passed） |
| [x] | TASK-004-07 | TASK-004-06 | 跑 `run-all-tests.sh fast` + `drift-check.sh` + `validate-specs.sh` | 三套门禁全通过（99 passed, 0 errors） |
| [ ] | TASK-004-08 | TASK-004-07 | （Phase C）生成诊断脚本，量化改进前后 median_m 对比 | 诊断报告 + median_m 改进量化 |

## 推荐执行批次

### 批次 P0：平面检测模块

`TASK-004-01` → `TASK-004-02` → `TASK-004-03`

### 批次 P1：分层 H 集成

`TASK-004-04` → `TASK-004-05` → `TASK-004-06`

### 批次 P2：门禁 + 量化

`TASK-004-07` → `TASK-004-08`
