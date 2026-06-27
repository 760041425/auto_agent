"""
LAS 点云多视角投影生成器

策略：
- 512x512 像素 tiles
- 10m 分辨率（每个像素对应 10mx10m 地面区域）
- 按高度分层：ground(0-25m), mid(25-60m), high(60-200m)
- 多视角：顶视图 + 4个方向斜视图（NE/NW/SE/SW）
- 每张图独立保存 + 像素→3D坐标映射
"""
import json
from pathlib import Path
from collections import defaultdict

import cv2
import numpy as np
from laspy import open as las_open
from PIL import Image, ImageEnhance


# ── 配置 ──────────────────────────────────────────────
TILE_SIZE = 512          # 像素
RESOLUTION = 10.0         # 米/像素
TILE_M = TILE_SIZE * RESOLUTION  # 5120m

# 高度分层（Z 单位：米）
Z_LAYERS = [
    (0, 25, "ground",    (65, 105, 225)),    # 地面层 - 蓝色
    (25, 60, "mid",      (34, 139, 34)),     # 中层 - 绿色
    (60, 200, "high",    (139, 69, 19)),     # 高层 - 棕色
]

# 斜视角角度（俯仰角，度）
VIEW_ANGLES = {
    "top":     (90, 0),    # 顶视图
    "ne_view": (45, 45),   # 东北方向斜视
    "nw_view": (45, 135),  # 西北方向斜视
    "se_view": (45, -45),  # 东南方向斜视
    "sw_view": (45, -135), # 西南方向斜视
}


def _project_points(x, y, z, x_min, y_min, res, pitch_deg=90, yaw_deg=0):
    """
    将 3D 点投影到 2D 图像坐标。
    pitch=90: 顶视图（正射）
    pitch=45: 45度斜视
    """
    pitch_rad = np.deg2rad(pitch_deg)
    yaw_rad = np.deg2rad(yaw_deg)

    # 平移使原点在左下角
    x_local = x - x_min
    y_local = y - y_min

    if pitch_deg >= 89:
        # 顶视图：直接映射
        col = (x_local / res).astype(np.int64)
        row = (y_local / res).astype(np.int64)
        return col, row, z
    else:
        # 斜视：先旋转（绕 Z 轴），再倾斜（绕 X 轴）
        cos_yaw = np.cos(yaw_rad)
        sin_yaw = np.sin(yaw_rad)
        cos_pitch = np.cos(pitch_rad)
        sin_pitch = np.sin(pitch_rad)

        # 绕 Z 轴旋转（水平旋转）
        x_rot = x_local * cos_yaw - y_local * sin_yaw
        y_rot = x_local * sin_yaw + y_local * cos_yaw

        # 绕 X 轴倾斜（俯仰）
        # 斜视角下，远处的点会往上偏移
        y_tilt = y_rot * cos_pitch - (z - z.min()) * sin_pitch
        z_tilt = y_rot * sin_pitch + (z - z.min()) * cos_pitch

        # 投影到图像平面
        col = (x_rot / res).astype(np.int64)
        row = (y_tilt / res).astype(np.int64)

        return col, row, z_tilt


def _render_tile(pixel_map, width, height):
    """将像素映射渲染为 JET 伪彩色图像"""
    img = np.zeros((height, width, 3), dtype=np.uint8)
    depth_img = np.full((height, width), np.nan, dtype=np.float32)

    for (px, py), (x, y, z) in pixel_map.items():
        if 0 <= px < width and 0 <= py < height:
            depth_img[py, px] = z

    valid = ~np.isnan(depth_img)
    if not valid.any():
        return None

    d_min, d_max = np.nanmin(depth_img), np.nanmax(depth_img)
    if d_max <= d_min:
        return None

    norm = np.zeros((height, width), dtype=np.uint8)
    norm[valid] = (255 * (depth_img[valid] - d_min) / (d_max - d_min)).astype(np.uint8)

    # JET 伪彩色
    img = cv2.applyColorMap(norm, cv2.COLORMAP_JET)

    # 边缘增强
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    sobelx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    sobely = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    edges = np.sqrt(sobelx ** 2 + sobely ** 2)
    if edges.max() > 0:
        edges = (edges / edges.max() * 40).clip(0, 255).astype(np.uint8)
        for c in range(3):
            img[:, :, c] = np.clip(img[:, :, c].astype(np.int32) + edges, 0, 255).astype(np.uint8)

    # 对比度+锐度
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    pil = ImageEnhance.Contrast(pil).enhance(1.5)
    pil = ImageEnhance.Sharpness(pil).enhance(2.0)
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


def project_las_multi_view(
    las_path: str,
    output_dir: str = "projections",
):
    """
    生成多视角 LAS 投影图集。

    返回: list[dict], 每个 dict 描述一张图的信息
    """
    reader = las_open(las_path)
    pts = reader.read()
    total = len(pts.x)

    # 采样
    step = max(1, total // 3_000_000)
    x = np.array(pts.x[::step], dtype=np.float64)
    y = np.array(pts.y[::step], dtype=np.float64)
    z = np.array(pts.z[::step], dtype=np.float64)

    x_min, x_max = float(x.min()), float(x.max())
    y_min, y_max = float(y.min()), float(y.max())

    # 分块
    n_cols = max(1, int(np.ceil((x_max - x_min) / TILE_M)))
    n_rows = max(1, int(np.ceil((y_max - y_min) / TILE_M)))

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # 清理旧文件
    import glob as glob_mod
    for f in list(glob_mod.glob(str(out / "tile_*.jpg"))) + \
              list(glob_mod.glob(str(out / "coord_tile_*.json"))) + \
              list(glob_mod.glob(str(out / "view_*.jpg"))) + \
              list(glob_mod.glob(str(out / "coord_view_*.json"))):
        Path(f).unlink(missing_ok=True)

    generated = []

    for layer_name, (z_min, z_max, layer_label, base_color) in enumerate(Z_LAYERS):
        # 筛选高度
        mask = (z >= z_min) & (z < z_max)
        x_layer = x[mask]
        y_layer = y[mask]
        z_layer = z[mask]

        if len(x_layer) < 50:
            continue

        layer_tag = Z_LAYERS[layer_name][2]

        for row in range(n_rows):
            for col in range(n_cols):
                tile_x_min = x_min + col * TILE_M
                tile_x_max = min(x_min + (col + 1) * TILE_M, x_max)
                tile_y_min = y_min + row * TILE_M
                tile_y_max = min(y_min + (row + 1) * TILE_M, y_max)

                # 取 tile 中心坐标
                tile_cx = (tile_x_min + tile_x_max) / 2
                tile_cy = (tile_y_min + tile_y_max) / 2
                half_m = TILE_SIZE * RESOLUTION / 2  # 2560m

                # 筛选 tile 内点（扩大范围确保覆盖512x512）
                mask_tile = (
                    (x_layer >= tile_cx - half_m) & (x_layer < tile_cx + half_m) &
                    (y_layer >= tile_cy - half_m) & (y_layer < tile_cy + half_m)
                )
                xt = x_layer[mask_tile]
                yt = y_layer[mask_tile]
                zt = z_layer[mask_tile]

                if len(xt) < 50:
                    continue

                # 固定 512x512（实际点云居中，周围留白）
                tile_w = TILE_SIZE
                tile_h = TILE_SIZE

                # ── 生成多个视角 ──
                for view_name, (pitch, yaw) in VIEW_ANGLES.items():
                    col_idx, row_idx, z_proj = _project_points(
                        xt, yt, zt,
                        tile_cx - half_m, tile_cy - half_m,
                        RESOLUTION, pitch, yaw
                    )

                    # 筛选在图像范围内的点
                    valid_mask = (col_idx >= 0) & (col_idx < tile_w) & (row_idx >= 0) & (row_idx < tile_h)
                    col_v = col_idx[valid_mask]
                    row_v = row_idx[valid_mask]
                    z_v = z_proj[valid_mask]
                    x_v = xt[valid_mask]
                    y_v = yt[valid_mask]

                    if len(col_v) < 30:
                        continue

                    # 构建像素映射（每个像素取 Z 中位数点）
                    pixel_bins = defaultdict(list)
                    for i in range(len(col_v)):
                        pixel_bins[(int(col_v[i]), int(row_v[i]))].append(
                            (float(x_v[i]), float(y_v[i]), float(z_v[i]))
                        )

                    pixel_map = {}
                    for (px, py), pts_list in pixel_bins.items():
                        arr = np.array(pts_list)
                        z_sorted = np.argsort(arr[:, 2])
                        median_idx = z_sorted[len(z_sorted) // 2]
                        pixel_map[(px, py)] = arr[median_idx].tolist()

                    # 渲染
                    img = _render_tile(pixel_map, tile_w, tile_h)
                    if img is None:
                        continue

                    # 翻转 Y
                    img = cv2.flip(img, 0)

                    # 坐标映射（Y 翻转后）
                    coord_map = {}
                    for (px, py), val in pixel_map.items():
                        coord_map[f"{px},{tile_h-1-py}"] = val

                    # 文件名
                    fname = f"view_{layer_tag}_{view_name}_{row}_{col}.jpg"
                    cname = f"coord_{layer_tag}_{view_name}_{row}_{col}.json"
                    img_path = str(out / fname)
                    coord_path = str(out / cname)

                    cv2.imwrite(img_path, img)
                    with open(coord_path, "w") as f:
                        json.dump({
                            "width": tile_w, "height": tile_h,
                            "resolution": RESOLUTION,
                            "layer": layer_tag,
                            "view": view_name,
                            "pitch": pitch, "yaw": yaw,
                            "tile_row": row, "tile_col": col,
                            "x_min": float(tile_x_min), "x_max": float(tile_x_max),
                            "y_min": float(tile_y_min), "y_max": float(tile_y_max),
                            "z_min": float(z_min), "z_max": float(z_max),
                            "pixel_count": len(coord_map),
                            "pixels": coord_map,
                        }, f, separators=(",", ":"))

                    info = {
                        "image_path": img_path,
                        "coord_map_path": coord_path,
                        "width": tile_w, "height": tile_h,
                        "layer": layer_tag,
                        "view": view_name,
                        "tile": f"{row}_{col}",
                        "pixel_count": len(coord_map),
                        "z_range": [float(z_min), float(z_max)],
                    }
                    generated.append(info)

    # 保存索引
    index_path = out / "tile_index.json"
    with open(index_path, "w") as f:
        json.dump(generated, f, indent=2)

    return generated


# 向后兼容
project_las_to_image = project_las_multi_view
