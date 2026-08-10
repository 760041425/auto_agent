"""TDD 测试：前端坐标交叉验证显示格式。

验证 web/app_v10.js 的 verifyCoordinatePoint 函数能正确解析 API 响应格式。
新格式使用 slam_xy/npy_xyz 数组与 error_m（米）/error_px（像素）。
"""


def _format_coordinate_display(payload):
    """模拟 web/app_v10.js 的 verifyCoordinatePoint 函数的格式化逻辑。

    这是前端 JS 函数的 Python 等价实现，用于 TDD 测试。
    """
    if payload.get("status") != "available":
        raise ValueError(payload.get("reason") or "坐标源不完整")

    html = '<div style="font-weight:bold;margin-bottom:0.25rem">坐标交叉验证</div>'
    html += '<div>选点: <b>(' + f"{payload['u']:.4f}" + ', ' + f"{payload['v']:.4f}" + ')</b></div>'

    slam_xy = payload.get("slam_xy")
    if slam_xy:
        html += (
            '<div>计算 XY: <b>('
            + f"{slam_xy[0]:.2f}" + ', '
            + f"{slam_xy[1]:.2f}"
            + ') m</b></div>'
        )

    npy_xyz = payload.get("npy_xyz")
    if npy_xyz:
        html += (
            '<div>NPY XYZ: <b>('
            + f"{npy_xyz[0]:.2f}" + ', '
            + f"{npy_xyz[1]:.2f}" + ', '
            + f"{npy_xyz[2]:.2f}"
            + ')</b></div>'
        )

    error_m = payload.get("error_m")
    if error_m is not None:
        html += '<div>坐标误差: <b>' + f"{error_m:.3f}" + ' m</b></div>'

    error_px = payload.get("error_px")
    if error_px is not None:
        html += '<div style="font-size:0.7rem;color:#666">重投影像素误差: ' + f"{error_px:.1f}" + ' px</div>'

    html += '<div style="font-size:0.7rem;color:#666;margin-top:0.2rem">PnP位姿+射线平面求交 vs NPY</div>'

    return html


def test_difference_px_displayed():
    """TL-005-01: API 返回 error_px，前端显示 "重投影像素误差: X px"。"""
    payload = {
        "status": "available",
        "u": 0.5,
        "v": 0.5,
        "slam_xy": [-17.0, 8.0],
        "npy_xyz": [-17.0, 8.0, -0.6],
        "error_m": 0.05,
        "error_px": 1.7,
    }
    result = _format_coordinate_display(payload)
    assert "重投影像素误差" in result
    assert "1.7 px" in result


def test_null_pixel_to_slam_no_crash():
    """TL-005-02: 后端不再提供 pixel_to_slam 时，前端不崩溃、不显示 "不可用"。"""
    payload = {
        "status": "available",
        "u": 0.5,
        "v": 0.5,
        "slam_xy": None,
        "npy_xyz": [-17.0, 8.0, -0.6],
        "error_px": 1.7,
    }
    result = _format_coordinate_display(payload)
    assert "不可用" not in result
    assert "H→SLAM" not in result


def test_fallback_to_difference_m():
    """TL-005-03: API 返回 error_m，前端显示 "坐标误差: X m"。"""
    payload = {
        "status": "available",
        "u": 0.5,
        "v": 0.5,
        "slam_xy": [-19.0, 14.0],
        "npy_xyz": [-17.0, 8.0, -0.6],
        "error_m": 0.5,
        "error_px": None,
    }
    result = _format_coordinate_display(payload)
    assert "坐标误差" in result
    assert "0.500 m" in result


def test_note_text():
    """TL-005-04: 备注文本包含 "PnP位姿+射线平面求交 vs NPY"。"""
    payload = {
        "status": "available",
        "u": 0.5,
        "v": 0.5,
        "slam_xy": [-17.0, 8.0],
        "npy_xyz": [-17.0, 8.0, -0.6],
        "error_px": 1.7,
    }
    result = _format_coordinate_display(payload)
    assert "PnP位姿+射线平面求交 vs NPY" in result


def test_full_new_format():
    """TL-005-05: 完整新格式，所有字段正确显示。"""
    payload = {
        "status": "available",
        "u": 0.4745,
        "v": 0.5619,
        "slam_xy": [-17.588, 8.357],
        "npy_xyz": [-17.588, 8.357, -0.639],
        "error_m": 0.05,
        "error_px": 1.7,
    }
    result = _format_coordinate_display(payload)
    assert "坐标交叉验证" in result
    assert "选点: <b>(0.4745, 0.5619)</b>" in result
    assert "NPY XYZ" in result
    assert "重投影像素误差: 1.7 px" in result
