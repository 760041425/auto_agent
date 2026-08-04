# 004 实施任务

**全部已完成（2026-08-04）**。Phase A（平面检测）+ Phase B（归一化 + 联合 PnP 修复）+ Phase C（诊断量化）均已实施。

| 状态 | TASK-ID | 依赖 | 任务 | 完成证据 |
| --- | --- | --- | --- | --- |
| [x] | TASK-004-01 | 无 | 新建 `plane_detection.py`，实现 `segment_plane()` 骨架 | 文件存在，函数可导入 |
| [x] | TASK-004-02 | TASK-004-01 | 新建 `test_plane_detection.py`，写失败测试让它红 | pytest 红（ImportError） |
| [x] | TASK-004-03 | TASK-004-02 | 实现 `segment_plane()` 让它绿（RANSAC + 精化） | pytest 绿（7/7 passed） |
| [x] | TASK-004-04 | TASK-004-03 | 改 `verify_projection.py`：加 `plane_distance_threshold`/`plane_seed` 参数 + 分层单应 | 测试通过 |
| [x] | TASK-004-05 | TASK-004-04 | 新建 `test_layered_homography.py`，写失败测试 | pytest 红（TypeError） |
| [x] | TASK-004-06 | TASK-004-05 | 让分层 H 测试绿 | pytest 绿（7/7 passed） |
| [x] | TASK-004-07 | TASK-004-06 | 跑全量门禁 | 99 passed, 0 errors |
| [x] | TASK-004-08 | TASK-004-07 | 诊断脚本量化 median_m 改进 | reports/2026-08-04-homography-deviation-diagnosis.md |
| [x] | TASK-004-09 | TASK-004-07 | **【Phase B 新增】** 坐标归一化：新增 `_normalize_2d()`，在 `build_local_coordinate_transform_context` 拟 H 前对像素/世界坐标分别归一化 | 单点偏差从 40m 降到 9.5m |
| [x] | TASK-004-10 | TASK-004-09 | **【Phase B 新增】** 平面投影：新增 `build_plane_coordinate_frame()` + `project_points_to_plane()`，对齐 slam-map 的平面坐标系构建方法 | 地面点召回 20/20，立面点剔除 10/10 |
| [x] | TASK-004-11 | TASK-004-10 | **【Phase B 新增】** 修复 `is_pose_better`：加 `err_ratio_limit=2.0` 约束，避免联合 PnP 劣化单候选位姿 | 201 内点/104px 不再覆盖 164 内点/23px |
| [x] | TASK-004-12 | TASK-004-11 | **【Phase B 新增】** 调用方启用：`salad_roma_v2.py` / `salad_roma.py` 传入 `plane_distance_threshold=0.2` | 日志显示 `plane_segmentation=plane_detected, ground=104/201` |

## 推荐执行批次（历史）

### 批次 P0：平面检测模块

`TASK-004-01` → `TASK-004-02` → `TASK-004-03`

### 批次 P1：分层 H 集成

`TASK-004-04` → `TASK-004-05` → `TASK-004-06`

### 批次 P2：门禁 + 量化

`TASK-004-07` → `TASK-004-08`

### 批次 P3（Phase B 补充）：深层修复

`TASK-004-09`（坐标归一化）→ `TASK-004-10`（平面投影）→ `TASK-004-11`（is_pose_better）→ `TASK-004-12`（调用方启用）
