import json
from pathlib import Path
from collections import defaultdict

import cv2
import numpy as np
from laspy import open as las_open
from PIL import Image, ImageEnhance


def _make_tile_image(height, width, pixel_map, name=""):
    """生成单张投影图"""
    img = np.zeros((height, width, 3), dtype=np.uint8)
    depth_img = np.full((height, width), np.nan, dtype=np.float32)

    for (px, py), (x, y, z) in pixel_map.items():
        depth_img[py, px] = z

    # 深度归一化着色（伪彩色）
    valid = ~np.isnan(depth_img)
    if not valid.any():
        return None

    d_min, d_max = np.nanmin(depth_img), np.nanmax(depth_img)
    if d_max <= d_min:
        return None

    norm = (255 * (depth_img - d_min) / (d_max - d_min)).astype(np.uint8)

    # Jet 伪彩色
    jet = cv2.applyColorMap(norm, cv2.COLORMAP_JET)
    img = jet.copy()

    # 边缘增强
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    sobelx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    sobely = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    edges = np.sqrt(sobelx ** 2 + sobely ** 2)
    edges = (edges / (edges.max() + 1e-6) * 40).clip(0, 255).astype(np.uint8)
    for c in range(3):
        img[:, :, c] = np.clip(img[:, :, c].astype(np.int32) + edges, 0, 255).astype(np.uint8)

    # 对比度增强
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    pil = ImageEnhance.Contrast(pil).enhance(1.5)
    pil = ImageEnhance.Sharpness(pil).enhance(2.0)
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


def project_las_multi_view(
    las_path: str,
    output_dir: str = "projections",
    ground_resolution: float = 0.5,      # 地面投影分辨率
    tile_size: int = 1000,               # 每块区域大小（像素）
    z_layers: list | None = None,        # 高度分层
):
    """
    多视角LAS投影：
    - 按高度分层（地面/中/高）
    - 分区域（tile）
    - 每张图较小且清晰
    """
    if z_layers is None:
        z_layers = [(0, 30, "ground"), (30, 60, "mid"), (60, 200, "high")]

    reader = las_open(las_path)
    pts = reader.read()
    total = len(pts.x)

    # 采样（处理大量点云）
    step = max(1, total // 3_000_000)
    x = np.array(pts.x[::step])
    y = np.array(pts.y[::step])
    z = np.array(pts.z[::step])

    x_min, x_max = float(x.min()), float(x.max())
    y_min, y_max = float(y.min()), float(y.max())

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # 删除旧投影文件
    for old in out.glob("tile_*.jpg"):
        old.unlink()
    for old in out.glob("coord_tile_*.json"):
        old.unlink()
    old_proj = out / "las_projection.jpg"
    if old_proj.exists():
        old_proj.unlink()
    old_coord = out / "coord_map.json"
    if old_coord.exists():
        old_coord.unlink()

    # 清理旧特征文件
    old_feat = out / "las_features.npz"
    if old_feat.exists():
        old_feat.unlink()

    generated = []

    for z_min, z_max, layer_name in z_layers:
        # 筛选高度
        mask = (z >= z_min) & (z < z_max)
        x_layer = x[mask]
        y_layer = y[mask]
        z_layer = z[mask]

        if len(x_layer) < 100:
            continue

        # 计算分块
        x_range = x_max - x_min
        y_range = y_max - y_min
        tile_w_m = x_range / max(1, round(x_range / (tile_size * ground_resolution)))
        tile_h_m = y_range / max(1, round(y_range / (tile_size * ground_resolution)))

        n_cols = max(1, int(np.ceil(x_range / tile_w_m)))
        n_rows = max(1, int(np.ceil(y_range / tile_h_m)))

        for row in range(n_rows):
            for col in range(n_cols):
                tile_x_min = x_min + col * tile_w_m
                tile_x_max = min(x_min + (col + 1) * tile_w_m, x_max)
                tile_y_min = y_min + row * tile_h_m
                tile_y_max = min(y_min + (row + 1) * tile_h_m, y_max)

                # 筛选区域内点
                mask_tile = (
                    (x_layer >= tile_x_min) & (x_layer < tile_x_max) &
                    (y_layer >= tile_y_min) & (y_layer < tile_y_max)
                )
                xt = x_layer[mask_tile]
                yt = y_layer[mask_tile]
                zt = z_layer[mask_tile]

                if len(xt) < 50:
                    continue

                # 构建像素映射
                w = int(np.ceil((tile_x_max - tile_x_min) / ground_resolution))
                h = int(np.ceil((tile_y_max - tile_y_min) / ground_resolution))
                w = max(1, min(w, tile_size))
                h = max(1, min(h, tile_size))

                col_idx = ((xt - tile_x_min) / ground_resolution).astype(np.int64).clip(0, w - 1)
                row_idx = ((yt - tile_y_min) / ground_resolution).astype(np.int64).clip(0, h - 1)

                pixel_map = {}
                pixel_bins = defaultdict(list)
                for i in range(len(xt)):
                    pixel_bins[(int(col_idx[i]), int(row_idx[i]))].append(
                        (float(xt[i]), float(yt[i]), float(zt[i]))
                    )

                for (px, py), pts_list in pixel_bins.items():
                    arr = np.array(pts_list)
                    # 取 Z 中位数点
                    z_sorted = np.argsort(arr[:, 2])
                    median_idx = z_sorted[len(z_sorted) // 2]
                    pixel_map[(px, py)] = arr[median_idx].tolist()

                # 生成图像
                img = _make_tile_image(h, w, pixel_map, f"{layer_name}_{row}_{col}")
                if img is None:
                    continue

                # 翻转 Y
                img = cv2.flip(img, 0)
                # 翻转像素映射的 Y
                pixel_map_flipped = {}
                for (px, py), val in pixel_map.items():
                    pixel_map_flipped[f"{px},{h-1-py}"] = val

                fname = f"tile_{layer_name}_{row}_{col}.jpg"
                cname = f"coord_tile_{layer_name}_{row}_{col}.json"
                img_path = str(out / fname)
                coord_path = str(out / cname)

                cv2.imwrite(img_path, img)
                with open(coord_path, "w") as f:
                    json.dump({
                        "width": w, "height": h,
                        "resolution": ground_resolution,
                        "layer": layer_name,
                        "tile_row": row, "tile_col": col,
                        "x_min": float(tile_x_min), "x_max": float(tile_x_max),
                        "y_min": float(tile_y_min), "y_max": float(tile_y_max),
                        "z_min": float(z_min), "z_max": float(z_max),
                        "pixel_count": len(pixel_map_flipped),
                        "pixels": pixel_map_flipped,
                    }, f)

                generated.append({
                    "image_path": img_path,
                    "coord_map_path": coord_path,
                    "width": w, "height": h,
                    "layer": layer_name,
                    "tile": f"{row}_{col}",
                    "pixel_count": len(pixel_map_flipped),
                    "x_range": [float(tile_x_min), float(tile_x_max)],
                    "y_range": [float(tile_y_min), float(tile_y_max)],
                    "z_range": [float(z_min), float(z_max)],
                })

    # 保存索引
    index_path = out / "tile_index.json"
    with open(index_path, "w") as f:
        json.dump(generated, f, indent=2)

    return generated


# 保持向后兼容
project_las_to_image = project_las_multi_view
