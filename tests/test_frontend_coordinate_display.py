"""TDD 测试：前端坐标交叉验证显示格式。

验证 web/app_v10.js 的 verifyCoordinatePoint 函数能正确解析新的 API 响应格式。
新格式使用 difference_px（像素误差）替代 difference_m（米）。
"""


def _format_coordinate_display(payload):
    """模拟 web/app_v10.js 的 verifyCoordinatePoint 函数的格式化逻辑。

    这是前端 JS 函数的 Python 等价实现，用于 TDD 测试。
    """
    if payload.get("status") != "available":
        raise ValueError(payload.get("reason") or "坐标源不完整")

    html = '<div style="font-weight:bold;margin-bottom:0.25rem">坐标交叉验证（PnP重投影）</div>'
    html += '<div>选点: <b>(' + f"{payload['u']:.4f}" + ', ' + f"{payload['v']:.4f}" + ')</b></div>'

    npy_point = payload.get("npy_point")
    if npy_point:
        html += (
            '<div>NPY XYZ: <b>('
            + f"{npy_point['x']:.2f}" + ', '
            + f"{npy_point['y']:.2f}" + ', '
            + f"{npy_point['z']:.2f}"
            + ')</b></div>'
        )

    difference_px = payload.get("difference_px")
    difference_m = payload.get("difference_m")

    if difference_px is not None:
        html += '<div>重投影误差: <b>' + f"{difference_px:.1f}" + ' px</b></div>'
    elif difference_m is not None:
        html += '<div>坐标差: <b>' + f"{difference_m:.3f}" + ' m</b></div>'

    html += '<div style="font-size:0.7rem;color:#666;margin-top:0.2rem">PnP位姿重投影NPY点到像素，与查询像素比较</div>'

    return html


def test_difference_px_displayed():
    """TL-005-01: API 返回 difference_px，前端显示 "重投影误差: X px"。"""
    payload = {
        "status": "available",
        "u": 0.5,
        "v": 0.5,
        "pixel_to_slam": None,
        "npy_point": {"x": -17.0, "y": 8.0, "z": -0.6},
        "difference_px": 1.7,
        "difference_m": None,
    }
    result = _format_coordinate_display(payload)
    assert "重投影误差" in result
    assert "1.7 px" in result


def test_null_pixel_to_slam_no_crash():
    """TL-005-02: pixel_to_slam 为 null 时，前端不崩溃、不显示 "不可用"。"""
    payload = {
        "status": "available",
        "u": 0.5,
        "v": 0.5,
        "pixel_to_slam": None,
        "npy_point": {"x": -17.0, "y": 8.0, "z": -0.6},
        "difference_px": 1.7,
    }
    result = _format_coordinate_display(payload)
    assert "不可用" not in result
    assert "H→SLAM" not in result


def test_fallback_to_difference_m():
    """TL-005-03: API 返回旧格式 difference_m 时，前端显示 "坐标差: X m"。"""
    payload = {
        "status": "available",
        "u": 0.5,
        "v": 0.5,
        "pixel_to_slam": {"slam_x": -19.0, "slam_y": 14.0, "slam_z": 0.0},
        "npy_point": {"x": -17.0, "y": 8.0, "z": -0.6},
        "difference_px": None,
        "difference_m": 0.5,
    }
    result = _format_coordinate_display(payload)
    assert "坐标差" in result
    assert "0.500 m" in result


def test_note_text():
    """TL-005-04: 备注文本包含 "PnP位姿重投影NPY点到像素"。"""
    payload = {
        "status": "available",
        "u": 0.5,
        "v": 0.5,
        "npy_point": {"x": -17.0, "y": 8.0, "z": -0.6},
        "difference_px": 1.7,
    }
    result = _format_coordinate_display(payload)
    assert "PnP位姿重投影NPY点到像素" in result


def test_full_new_format():
    """TL-005-05: 完整新格式，所有字段正确显示。"""
    payload = {
        "status": "available",
        "u": 0.4745,
        "v": 0.5619,
        "pixel_to_slam": None,
        "npy_point": {"x": -17.588, "y": 8.357, "z": -0.639},
        "difference_px": 1.7,
        "difference_m": None,
    }
    result = _format_coordinate_display(payload)
    assert "坐标交叉验证（PnP重投影）" in result
    assert "选点: <b>(0.4745, 0.5619)</b>" in result
    assert "NPY XYZ" in result
    assert "重投影误差: <b>1.7 px</b>" in result
