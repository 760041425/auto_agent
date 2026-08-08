# 005 坐标交叉验证前端显示修复

状态：待实施
上下文：空间定位、坐标交叉验证

## 背景与目标

### 背景

`services/localizer/verify_projection.py` 的 `query_local_coordinate_transform` 函数已改用 **PnP 重投影**验证（参见 specs/004），API 返回格式变更：

| 旧格式 | 新格式 |
| --- | --- |
| `pixel_to_slam: {slam_x, slam_y, slam_z}` | `pixel_to_slam: null`（不再提供） |
| `difference_m: 6.79`（米） | `difference_px: 1.7`（像素） |
| `npy_point: {x, y, z}` | `npy_point: {x, y, z}`（不变） |

但前端 `web/app_v10.js` 行 699-708 仍使用旧格式显示，导致：
- **"H→SLAM XYZ: 不可用"**（`pixel_to_slam` 为 null 时 `formatCoordinateXYZ` 返回 "不可用"）
- **"坐标差: NaN m"**（`difference_m` 为 null 时 `Number(null)` = `NaN`）

### 目标

前端正确显示新的 PnP 重投影验证结果。

## 范围

### In Scope

- 修改 `web/app_v10.js` 的 `verifyCoordinatePoint` 函数
- 新增单元测试 `tests/test_frontend_coordinate_display.py`
- 更新 `contexts/spatial-localization/README.md` 术语

### Out Scope

- 不修改 API 返回格式（后端已正确）
- 不修改 PnP 重投影算法（后端已正确）

## 验收标准

- **AC-005-01**: API 返回 `difference_px=1.7`，前端显示 "重投影误差: 1.7 px"
- **AC-005-02**: API 返回 `pixel_to_slam=null`，前端不崩溃、不显示 "不可用"
- **AC-005-03**: API 返回旧格式 `difference_m=0.5`（回退场景），前端显示 "坐标差: 0.5 m"
- **AC-005-04**: 备注文本更新为 "PnP位姿重投影NPY点到像素，与查询像素比较"
- **AC-005-05**: 单元测试全部通过

## 成功标准

- 点击绿色凸包内任意点，显示正确的重投影误差（像素）
- 不再显示 "H→SLAM XYZ: 不可用" 或 "坐标差: NaN m"
- 快速测试、漂移检查、规格校验全部通过

## 关联

- `specs/004-plane-aware-homography/`（本次变更的根因）
- `web/app_v10.js` 行 699-708（被修改文件）
- `services/localizer/verify_projection.py` 行 644-710（API 返回格式）
