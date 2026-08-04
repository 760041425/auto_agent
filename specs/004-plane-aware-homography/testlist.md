# 004 测试清单

| 状态 | TL-ID | 映射 AC | 层级 | 场景与期望 |
| --- | --- | --- | --- | --- |
| [x] | **TL-004-01** | AC-004-01 | 单元 | `segment_plane` 在纯地面点（Z 方差 < 0.05m，N=20）输入时，返回 `(plane_params, inlier_mask)`，`inlier_mask` 全为 True，`plane_params` 法向量接近 (0,0,1) |
| [x] | **TL-004-02** | AC-004-01,AC-004-02 | 单元 | `segment_plane` 在地面+立面混合输入（地面 20 点 Z≈0，立面 5 点 Z>1.0m）时，地面点 inlier=True，立面点 inlier=False，地面内点占比 100%（20/20） |
| [x] | **TL-004-03** | AC-004-01 | 单元 | `segment_plane` 在点数 <4（N=3）时返回 `(None, None)` |
| [x] | **TL-004-04** | AC-004-01 | 单元 | `segment_plane` 在全部 NaN/Inf 输入时返回 `(None, None)`，不抛异常 |
| [x] | **TL-004-05** | AC-004-07 | 单元 | `segment_plane` 在同输入、同 seed 下多次调用返回完全相同结果（确定性） |
| [x] | **TL-004-06** | AC-004-01,AC-004-08 | 单元 | `segment_plane` 在 `distance_threshold=0.15m` 时能分离地面（±0.1m 平整）与立面（Z>0.5m）；在 `distance_threshold=0.3m` 时全部内点（阈值过宽） |
| [x] | **TL-004-07** | AC-004-03 | 单元 | `build_local_coordinate_transform_context` 在纯地面点输入时，行为与修改前一致（H 相同，`plane_segmentation.status=plane_detected`，`n_ground_inliers=N`） |
| [x] | **TL-004-08** | AC-004-03,AC-004-05 | 单元 | `build_local_coordinate_transform_context` 在地面+立面混合输入时，只用地面点拟合 H；query 点重投影到 SLAM XY 的中位误差 <0.1m（全点拟合同输入 >0.3m） |
| [x] | **TL-004-09** | AC-004-03,AC-004-04 | 单元 | `build_local_coordinate_transform_context` 在地面点不足 4 个时，回退到全点拟合，`plane_segmentation.status=insufficient_ground_points`，`n_ground_inliers=0` |
| [x] | **TL-004-10** | AC-004-03 | 单元 | `build_local_coordinate_transform_context` 函数签名向后兼容——不传 `plane_distance_threshold`/`plane_seed` 时行为与修改前一致（全点拟合） |
| [x] | **TL-004-11** | AC-004-06 | 单元 | `evaluate_local_coordinate_consistency` 在分层 H 下的 median_m ≤ 当前全点 H 下的 median_m（不退化）——用合成数据（地面 20 + 立面 5）验证 |
| [x] | **TL-004-12** | AC-004-03 | 单元 | `build_local_coordinate_transform_context` 在 `segment_plane` 返回 `(None, None)` 时（如全部 NaN），回退到全点拟合，不抛异常 |
| [x] | **TL-004-13** | AC-004-01 | 单元 | `segment_plane` 在斜面输入（Z=X*0.1+噪声，N=30）时，正确提取斜面内点，法向量与理论值误差 <5° |
| [x] | **TL-004-14** | AC-004-09 | 单元 | `_normalize_2d` 把任意范围 2D 点归一化到零均值、sqrt(2) 均方距离；返回的 T 矩阵满足 `normalized ≈ T @ original_homogeneous` |
| [x] | **TL-004-15** | AC-004-10 | 单元 | `build_plane_coordinate_frame` 从平面方程返回原点 + 正交单位切向量；法向量≈[0,0,1] 时 x/y_axis ≈ world X/Y |
| [x] | **TL-004-16** | AC-004-10 | 单元 | `project_points_to_plane` 把 3D 点投影到平面，返回的 2Z 坐标 Z 方向残差 < 距离阈值 |
| [x] | **TL-004-17** | AC-004-11 | 单元 | `is_pose_better` 当候选误差 > 当前误差 × 2.0 时返回 False（即使内点更多） |
| [x] | **TL-004-18** | AC-004-11 | 单元 | `is_pose_better` 当候选内点更多且误差未超限时返回 True（正常择优） |
| [x] | **TL-004-19** | AC-004-12 | 集成 | `salad_roma_v2.py` 调用 `build_local_coordinate_transform_context` 时传入 `plane_distance_threshold=0.2` |

## TDD 顺序

### 批次 P0：平面检测（TL-004-01 至 TL-004-06）

1. 写 TL-004-01（纯地面全内点）→ 红 → 实现 → 绿
2. 写 TL-004-02（混合分割）→ 红 → 实现 → 绿
3. 写 TL-004-03（点数不足）→ 红 → 实现 → 绿
4. 写 TL-004-04（NaN 输入）→ 红 → 实现 → 绿
5. 写 TL-004-05（确定性）→ 红 → 实现 → 绿
6. 写 TL-004-06（阈值敏感）→ 红 → 实现 → 绿

### 批次 P1：分层 H 集成（TL-004-07 至 TL-004-13）

1. 写 TL-004-07（纯地面一致）→ 红 → 改 `verify_projection.py` → 绿
2. 写 TL-004-08（混合精度提升）→ 红 → 改 `verify_projection.py` → 绿
3. 写 TL-004-09（退化回退）→ 红 → 改 `verify_projection.py` → 绿
4. 写 TL-004-10（向后兼容）→ 红 → 改 `verify_projection.py` → 绿
5. 写 TL-004-11（一致性不退化）→ 红 → 改 `verify_projection.py` → 绿
6. 写 TL-004-12（None 回退）→ 红 → 改 `verify_projection.py` → 绿
7. 写 TL-004-13（斜面）→ 红 → 改 `verify_projection.py` → 绿
