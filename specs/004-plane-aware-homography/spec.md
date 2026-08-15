# 004 平面感知分层单应性 + 坐标归一化修复

状态：**已实施（2026-08-04）**
上下文：空间定位、本地坐标转换、可信判定

## 背景与目标

### 原始问题（Phase A）

当前 `services/localizer/verify_projection.py` 的 `build_local_coordinate_transform_context` 函数直接用**全部** PnP 内点（`best_3d`）调用 `cv2.findHomography` 拟合 query 像素 → SLAM XY 的单应矩阵 H，并隐式强制 Z=0。

但 `best_3d` 实际包含地面点、立面点、高处点（Z 从 0 到几米），**并不共面**。把它们混在一起喂给 `findHomography`，相当于让立面点和高处点「投票」出一个平均平面，导致 H→SLAM XY 与 NPY XY 差很多。

### 深层问题（Phase B — 实施中发现）

实施 Phase A 后真实数据测试发现**两个额外根因**：

1. **坐标归一化缺失**：像素坐标（0~512）与 SLAM XY（米制十几~几十）量级差 10-100 倍，直接喂给 `cv2.findHomography` 即使地面点共面也会数值不稳定，导致 H 拟合偏差数十米。
2. **联合 PnP 劣化位姿**：`salad_roma_v2.py` 的多候选联合 PnP 把不同 tile 的 3D 坐标混在一起求解，`is_pose_better()` 只看内点数不看误差，导致 201 内点/104px 的联合结果覆盖了 164 内点/23px 的单候选结果，最终位姿误差 4 倍增长。

### 症状

- PnP 输出的角度（旋转向量）看似正确——用户视觉检查投影图像「角度差不多」
- 但 `evaluate_local_coordinate_consistency` 用 H 计算的 SLAM XY 与 NPY XY 的中位差 37m（正常应 <0.3m）
- 最终 `reliable=false` 不是位姿真的差，而是 H 映射被（a）立面点拉歪（b）数值不稳定（c）联合 PnP 劣化位姿 三重原因导致

### 目标

1. 在拟合 H 之前先做 RANSAC 平面分割，只用地面内点拟合 H
2.  Hartley 归一化消除像素与 SLAM XY 量级差
3. 对齐 slam-map 的平面投影方法（构建平面 2D 坐标系，投影 3D 点到平面）
4. 修复 `is_pose_better` 避免联合 PnP 劣化单候选位姿

## 范围

### In Scope

- 新增 `plane_detection.py` 模块：`segment_plane()` + `build_plane_coordinate_frame()` + `project_points_to_plane()`
- 修改 `build_local_coordinate_transform_context()`：平面检测 + 分层单应 + 坐标归一化 + 平面投影
- 修改 `pose_utils.py` 的 `is_pose_better()`：加误差上限约束
- 修改 `salad_roma_v2.py` / `salad_roma.py`：调用方传入 `plane_distance_threshold=0.2`
- 新增 `test_plane_detection.py`、`test_layered_homography.py` 两套 TDD 测试
- 诊断脚本 `scripts/diagnose_homography_deviation.py` 量化改进前后 median_m 变化

### Out of Scope

- 不修改 PnP 求解核心流程（`solve_pnp_ransac` 本身不改）
- 不修改 `evaluate_local_coordinate_consistency()` 的判定逻辑（仍用中位差 <0.3m）
- 不修改 API 契约和前端展示
- 不引入多平面分割（只取最大平面=地面）

## 用户故事

- 作为算法工程师，我希望「位姿本身可靠但 H 被立面点拉歪」的场景不再被误判为 `reliable=false`。
- 作为维护者，我希望平面分割逻辑可独立测试，不耦合在 H 拟合函数内部。
- 作为用户，我希望地面上稠密的匹配点真正决定 H 的精度，而不是被高处窗户/招牌点平均掉。
- 作为用户，我希望坐标归一化后 H 拟合数值稳定，不会因为像素/米制量级差产生几十米偏差。
- 作为用户，我希望 `is_pose_better` 不会因为内点数多一点就采纳误差翻倍的劣化位姿。

## 验收标准

- **AC-004-01**：`plane_detection.segment_plane(points_3d, distance_threshold=0.2, min_inliers=4, seed=1337)` 返回 `(plane_params, inlier_mask)`，`plane_params=(a,b,c,d)` 为归一化平面，`inlier_mask` 为 bool 数组。失败返回 `(None, None)`。
- **AC-004-02**：纯地面点 inlier_mask 全 True；地面+立面混合时地面内点占比 >70%。
- **AC-004-03**：`build_local_coordinate_transform_context` 函数签名不变（无 breaking change），内部先调用 `segment_plane` 过滤；地面内点 ≥4 时只用地面点拟合 H；地面内点 <4 时回退到全部点拟合。
- **AC-004-04**：返回的 `context` 中增加 `plane_segmentation` 字段，记录 `status=insufficient_ground_points` 或 `status=plane_detected,n_ground_inliers=N`。
- **AC-004-05**：合成数据上分层 H 中位误差 <0.1m；全点拟合 >0.3m。
- **AC-004-06**：`evaluate_local_coordinate_consistency` 在分层 H 下的 median_m ≤ 当前全点 H 下的 median_m（不退化）。
- **AC-004-07**：RANSAC 随机种子固定（默认 1337），同一输入多次调用返回相同结果。
- **AC-004-08**：`segment_plane` 距离阈值可配置，默认 0.2m。
- **AC-004-09**（新增）：`build_local_coordinate_transform_context` 在拟 H 前对像素坐标和世界坐标做 Hartley 归一化（`_normalize_2d`），消除量级差。
- **AC-004-10**（新增）：`plane_detection.build_plane_coordinate_frame()` + `project_points_to_plane()` 对齐 slam-map 的平面坐标系构建方法，把 3D 点投影到检测出的平面上得到真正的平面坐标（而非简单取 XY）。
- **AC-004-11**（新增）：`is_pose_better` 加 `err_ratio_limit=2.0` 约束——候选误差超过当前误差 2 倍时，即使内点更多也拒绝。
- **AC-004-12**（新增）：`salad_roma_v2.py` / `salad_roma.py` 调用方传入 `plane_distance_threshold=0.2, plane_seed=1337` 启用平面检测。

## 成功标准

- 合成混合数据下，分层 H 的中位误差比全点 H 降低 50% 以上
- 真实场景复跑后，原本因 H 拉歪导致 median_m >0.3m 的 case 有至少 50% 降到 <0.3m
- 地面点不足的回退路径有测试覆盖，不引入新的 `not_available`
- 坐标归一化后 H 拟合数值稳定，像素/米制量级差不产生偏差
- `is_pose_better` 不再选择误差翻倍的劣化位姿
- 快速测试、漂移检查、规格校验全部通过

## 关联

- `specs/003-multi-algo-verification/`（坐标差判据、Z 维度修复）
- `services/localizer/verify_projection.py`（被修改文件）
- `services/localizer/plane_detection.py`（新建文件）
- `services/localizer/pose_utils.py`（`is_pose_better` 修改）
- `services/localizer/salad_roma_v2.py` / `salad_roma.py`（调用方修改）
- `/Users/pangjinfu/code/slam-map/slam-map-engine/engine/compute_homography_from_salad.py`（参考实现）
