# 005 澄清问题

## CL-005-01：旧格式兼容性

**问题**：是否需要兼容旧格式（`difference_m`/`pixel_to_slam`/`npy_point`）？

**决定**：已实现为真源契约字段：`error_m`（米）、`error_px`（像素）、`slam_xy`（数组）、`npy_xyz`（数组），`pixel_to_slam` 不再提供（null）。

**理由**：
- 后端 `query_local_coordinate_transform` 只输出 `error_m`/`error_px`/`slam_xy`/`npy_xyz`
- 前端 `verifyCoordinatePoint` 只读取这些新字段


## CL-005-02：备注文本

**问题**：备注文本应该是什么？

**决定**：`PnP位姿+射线平面求交 vs NPY`

**理由**：与后端返回的 `note` 字段一致，准确描述当前验证方式。

## CL-005-03：测试层级

**问题**：前端 JS 测试用什么框架？

**决定**：纯 Python 测试（模拟 API 响应，验证格式化函数），不需要浏览器测试。

**理由**：
- 项目无 JS 测试框架
- 格式化逻辑可独立测试
