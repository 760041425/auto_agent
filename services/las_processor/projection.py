"""
LAS 点云分块真实高度投影

策略：
- 分块投影，每块 256x256m → 512x512 像素（0.5m 分辨率）
- 每个像素取 Z 最低的点（最接近地面），保留真实 Z 值
- 不做任何平均/中位数/平滑
- 输出：JPG（高度伪彩色）+ JSON（像素→3D坐标映射）
"""
import json
from pathlib import Path
from collections import defaultdict

import cv2
import numpy as np
from laspy import open as las_open
from PIL import Image, ImageEnhance


TILE_PX = 512          # 每块像素
TILE_M = 20            # 每块地面尺寸 (m)
RES = TILE_M / TILE_PX # 分辨率 ≈ 0.039m/像素 (3.9cm)


def project_las_multi_view(
    las_path: str,
    output_dir: str = "projections",
):
    """
    分块投影 LAS 点云，每块 512x512 像素。
    每个像素取 Z 最低的点，保留真实 3D 坐标。
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

    n_cols = max(1, int(np.ceil((x_max - x_min) / TILE_M)))
    n_rows = max(1, int(np.ceil((y_max - y_min) / TILE_M)))

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # 清理旧文件
    for pattern in ["tile_*.jpg", "coord_tile_*.json"]:
        for f in out.glob(pattern):
            f.unlink(missing_ok=True)

    generated = []

    for row in range(n_rows):
        for col in range(n_cols):
            tx_min = x_min + col * TILE_M
            tx_max = min(x_min + (col + 1) * TILE_M, x_max)
            ty_min = y_min + row * TILE_M
            ty_max = min(y_min + (row + 1) * TILE_M, y_max)

            # 筛选 tile 内点
            mask = (x >= tx_min) & (x < tx_max) & (y >= ty_min) & (y < ty_max)
            xt = x[mask]
            yt = y[mask]
            zt = z[mask]

            if len(xt) < 50:
                continue

            w = int(np.ceil((tx_max - tx_min) / RES))
            h = int(np.ceil((ty_max - ty_min) / RES))
            w = min(w, TILE_PX)
            h = min(h, TILE_PX)

            col_idx = ((xt - tx_min) / RES).astype(np.int64).clip(0, w - 1)
            row_idx = ((yt - ty_min) / RES).astype(np.int64).clip(0, h - 1)

            # 对每个像素，取 Z 最低的点（最接近地面）
            # 用 dict: (px, py) -> (x, y, z)  保留第一个遇到的最低Z点
            pixel_map = {}
            for i in range(len(xt)):
                px, py = int(col_idx[i]), int(row_idx[i])
                key = (px, py)
                if key not in pixel_map or zt[i] < pixel_map[key][2]:
                    pixel_map[key] = (float(xt[i]), float(yt[i]), float(zt[i]))

            if len(pixel_map) < 20:
                continue

            # 渲染图像（Z 伪彩色）
            img = np.zeros((h, w, 3), dtype=np.uint8)
            depth_img = np.full((h, w), np.nan, dtype=np.float32)

            for (px, py), (_, _, pz) in pixel_map.items():
                depth_img[py, px] = pz

            valid = ~np.isnan(depth_img)
            d_min, d_max = np.nanmin(depth_img[valid]), np.nanmax(depth_img[valid])

            if d_max > d_min:
                norm = np.zeros((h, w), dtype=np.uint8)
                norm[valid] = (255 * (depth_img[valid] - d_min) / (d_max - d_min)).astype(np.uint8)
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

                # 对比度
                pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
                pil = ImageEnhance.Contrast(pil).enhance(1.5)
                pil = ImageEnhance.Sharpness(pil).enhance(2.0)
                img = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)

            # 翻转 Y
            img = cv2.flip(img, 0)

            # 坐标映射（Y 翻转后）
            coord_map = {}
            for (px, py), (rx, ry, rz) in pixel_map.items():
                coord_map[f"{px},{h-1-py}"] = [rx, ry, rz]

            fname = f"tile_{row}_{col}.jpg"
            cname = f"coord_tile_{row}_{col}.json"

            cv2.imwrite(str(out / fname), img)
            with open(str(out / cname), "w") as f:
                json.dump({
                    "width": w, "height": h,
                    "resolution": RES,
                    "tile_row": row, "tile_col": col,
                    "x_min": float(tx_min), "x_max": float(tx_max),
                    "y_min": float(ty_min), "y_max": float(ty_max),
                    "pixel_count": len(coord_map),
                    "pixels": coord_map,
                }, f, separators=(",", ":"))

            generated.append({
                "image_path": str(out / fname),
                "coord_map_path": str(out / cname),
                "width": w, "height": h,
                "tile": f"{row}_{col}",
                "pixel_count": len(coord_map),
            })

    # 保存索引
    with open(str(out / "tile_index.json"), "w") as f:
        json.dump(generated, f, indent=2)

    return generated


# 向后兼容
project_las_to_image = project_las_multi_view
