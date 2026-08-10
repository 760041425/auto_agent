# 005 坐标交叉验证前端显示修复

状态：待实施
上下文：空间定位、坐标交叉验证

## 背景与目标

### 背景

`services/localizer/verify_projection.py` 的 `query_local_coordinate_transform` 函数已改用 **PnP 重投影**验证（参见 specs/004），API 返回格式变更：

| 旧格式 | 新格式 |
| --- | --- |
| `pixel_to_slam: {slam_x, slam_y, slam_z}` | 不再提供 → `slam_xy: [x, y]`（数组） |
| `difference_m: 6.79`（米） | `error_m: 6.79`（米） |
| `difference_px: 1.7`（像素） | `error_px: 1.7`（像素） |
| `npy_point: {x, y, z}` | `npy_xyz: [x, y, z]`（数组） |

但前端 `web/app_v10.js` 行 699-731 的 `verifyCoordinatePoint` 需按新格式读取，否则会因旧键名取不到值导致：
- **"计算 XY: 不可用"**（`slam_xy` 无法从旧 `pixel_to_slam` 读取时）
- **"坐标误差: 不显示"**（`error_m` 为 null 时无米制误差文案）

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

- **AC-005-01**: API 返回 `error_px=1.7`，前端显示 "重投影像素误差: 1.7 px"
- **AC-005-02**: API 不再返回 `pixel_to_slam`（`slam_xy` 可为 null），前端不崩溃、不显示 "不可用"
- **AC-005-03**: API 返回 `error_m=0.5`（坐标误差），前端显示 "坐标误差: 0.500 m"
- **AC-005-04**: 备注文本更新为 "PnP位姿+射线平面求交 vs NPY"
- **AC-005-05**: 单元测试全部通过

## 成功标准

- 点击绿色凸包内任意点，显示正确的重投影像素误差（`error_px`）与坐标误差（`error_m`）
- 不再显示旧格式的 "坐标差 / H→SLAM XYZ" 文案，只显示 `error_px`/`error_m`/`slam_xy`/`npy_xyz` 新格式
- 快速测试、漂移检查、规格校验全部通过

## 关联

- `specs/004-plane-aware-homography/`（本次变更的根因）
- `web/app_v10.js` 行 699-731（被修改文件）
- `services/localizer/verify_projection.py` 行 651-749（API 返回格式）
