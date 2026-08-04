# 004 平面感知分层单应性修复

状态：Phase A 待实施
上下文：空间定位、本地坐标转换、可信判定

## 背景与目标

当前 `services/localizer/verify_projection.py` 的 `build_local_coordinate_transform_context` 函数直接用**全部** PnP 内点（`best_3d`）调用 `cv2.findHomography` 拟合 query 像素 → SLAM XY 的单应矩阵 H，并隐式强制 Z=0。

但 `best_3d` 实际包含地面点、立面点、高处点（Z 从 0 到几米），**并不共面**。把它们混在一起喂给 `findHomography`，相当于让立面点和高处点「投票」出一个平均平面，导致 H→SLAM XY 与 NPY XY 差很多。

**症状**：
- PnP 输出的角度（旋转向量）是正确的——3D 点云本身坐标是对的、PnP 求解的相机位姿也是对的
- 但 `evaluate_local_coordinate_consistency` 用 H 计算的 SLAM XY 与 NPY XY 的中位差很大（远大于 0.3m）
- 最终 `reliable=false` 不是位姿真的差，而是 H 这个「像素→地面平面」映射被非地面点拉歪了

**目标**：在拟合 H 之前先做 RANSAC 平面分割，只用地面内点拟合 H，让 H→SLAM XY 真正反映 NPY XY，从而让多点一致性判据能正确识别「位姿本身可靠」的结果。

## 范围

### In Scope

- 新增 `segment_plane()` 函数，对 PnP 内点的 3D 坐标做 RANSAC 平面分割，返回地面内点掩码
- 修改 `build_local_coordinate_transform_context()` 内部行为：先调用 `segment_plane()` 过滤，只用地面内点拟合 H；地面点不足 4 个时回退到当前全点拟合
- 新增 `plane_detection.py` 模块，封装 RANSAC 平面分割逻辑
- 新增 `test_plane_detection.py`、`test_layered_homography.py` 两套 TDD 测试
- 诊断脚本（Phase C 另行生成）量化改进前后 median_m 变化

### Out of Scope

- 不修改 `query_local_coordinate_transform()` 单点查询路径（单点查询仍使用 H→SLAM XY，与当前行为一致）
- 不修改 PnP 求解流程（位姿输出不变）
- 不修改 `evaluate_local_coordinate_consistency()` 的判定逻辑（仍用中位差 <0.3m）
- 不引入多平面分割（只取最大平面=地面）
- 不引入点云法线估计或区域生长
- 不修改 API 契约和前端展示

## 用户故事

- 作为算法工程师，我希望「位姿本身可靠但 H 被立面点拉歪」的场景不再被误判为 `reliable=false`。
- 作为维护者，我希望平面分割逻辑可独立测试，不耦合在 H 拟合函数内部。
- 作为用户，我希望地面上稠密的匹配点真正决定 H 的精度，而不是被高处窗户/招牌点平均掉。

## 验收标准

- **AC-004-01**：新增 `plane_detection.segment_plane(points_3d, distance_threshold=0.2, min_inliers=4, seed=1337)` 函数，对 `(N, 3)` 世界坐标返回 `(plane_params, inlier_mask)`，其中 `plane_params=(a,b,c,d)` 为 `ax+by+cz+d=0` 归一化平面，`inlier_mask` 为长度 N 的 bool 数组。当点数不足 4 或 RANSAC 失败时返回 `(None, None)`。
- **AC-004-02**：`segment_plane` 在纯地面点（Z 方差 < 0.05m）输入时，inlier_mask 全为 True；在地面+立面混合输入时（地面 Z≈0，立面 Z>1.0m），地面点 inlier=True，立面点 inlier=False，且地面内点占比 >70%。
- **AC-004-03**：`build_local_coordinate_transform_context` 函数签名不变（无 breaking change），但内部行为变更：先调用 `segment_plane` 提取地面内点；地面内点 ≥4 时只用地面点拟合 H；地面内点 <4 时回退到全部点拟合（保留当前行为）。
- **AC-004-04**：地面内点不足 4 个时的回退路径可观察——返回的 `context` 中增加 `plane_segmentation` 字段，记录 `status=insufficient_ground_points` 或 `status=plane_detected,n_ground_inliers=N`。
- **AC-004-05**：在合成数据上（地面 20 点 + 立面 5 点，H 真值已知），分层 H 拟合后 query 点重投影到 SLAM XY 的中位误差 <0.1m；全点拟合同输入的中位误差 >0.3m。
- **AC-004-06**：`evaluate_local_coordinate_consistency` 在分层 H 下的 median_m ≤ 当前全点 H 下的 median_m（不退化）。
- **AC-004-07**：RANSAC 随机种子固定（默认 1337），同一输入多次调用返回相同结果。
- **AC-004-08**：`segment_plane` 距离阈值可配置，默认 0.2m；在 0.15m–0.3m 范围内能正确分割标准 LAS 地面点（地面平整度 ±0.1m）与立面点（Z>0.5m）。

## 成功标准

- 合成混合数据下，分层 H 的中位误差比全点 H 降低 50% 以上
- 真实场景（task #249 等）复跑后，原本因 H 拉歪导致 median_m 在 0.3m–1.0m 之间的 case 有至少 50% 降到 <0.3m（位姿本身可靠时被正确判为 `reliable=true`）
- 地面点不足的回退路径有测试覆盖，不引入新的 `not_available`
- 快速测试、漂移检查、规格校验全部通过

## 关联

- `specs/003-multi-algo-verification/`（坐标差判据、Z 维度修复）
- `services/localizer/verify_projection.py`（被修改文件）
- `services/localizer/salad_roma_v2.py` 行 928-970（调用方）
- `services/localizer/pose_utils.py`（共享几何工具，无现成平面工具）
