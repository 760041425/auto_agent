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
from PIL import Image, ImageEnhance, ImageFilter


TILE_PX = 512          # 每块像素
TILE_M = 50            # 每块地面尺寸 (m)
RES = TILE_M / TILE_PX # 分辨率 ≈ 0.098m/像素 (9.8cm)


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

    # 采样（同时读 RGB）
    step = max(1, total // 3_000_000)
    x = np.array(pts.x[::step], dtype=np.float64)
    y = np.array(pts.y[::step], dtype=np.float64)
    z = np.array(pts.z[::step], dtype=np.float64)
    has_rgb = hasattr(pts, 'red') and hasattr(pts, 'green') and hasattr(pts, 'blue')
    r_arr = np.array(pts.red[::step], dtype=np.uint8) if has_rgb else None
    g_arr = np.array(pts.green[::step], dtype=np.uint8) if has_rgb else None
    b_arr = np.array(pts.blue[::step], dtype=np.uint8) if has_rgb else None

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

            # 先筛选 RGB
            rt = r_arr[mask] if r_arr is not None else None
            gt = g_arr[mask] if g_arr is not None else None
            bt = b_arr[mask] if b_arr is not None else None

            # 渲染图像：所有地面附近的点都画上（过滤超高）
            img = np.zeros((h, w, 3), dtype=np.uint8)
            z_cap = 80.0
            for i in range(len(xt)):
                px, py = int(col_idx[i]), int(row_idx[i])
                if px < 0 or px >= w or py < 0 or py >= h:
                    continue
                if zt[i] > z_cap:
                    continue
                cr = int(rt[i]) if rt is not None else 128
                cg = int(gt[i]) if gt is not None else 128
                cb = int(bt[i]) if bt is not None else 128
                img[py, px, 0] = np.clip(cb, 0, 255)
                img[py, px, 1] = np.clip(cg, 0, 255)
                img[py, px, 2] = np.clip(cr, 0, 255)

            # 坐标映射：取 Z 最低的点，但排除超高异常点（Z>80m视为噪声/飞点）
            pixel_map = {}
            z_cap = 80.0  # 忽略高于80m的点（避免建筑/飞点干扰）
            for i in range(len(xt)):
                px, py = int(col_idx[i]), int(row_idx[i])
                if px < 0 or px >= w or py < 0 or py >= h:
                    continue
                if zt[i] > z_cap:  # 过滤超高
                    continue
                key = (px, py)
                if key not in pixel_map or zt[i] < pixel_map[key][2]:
                    cr = int(rt[i]) if rt is not None else 0
                    cg = int(gt[i]) if gt is not None else 0
                    cb = int(bt[i]) if bt is not None else 0
                    pixel_map[key] = (float(xt[i]), float(yt[i]), float(zt[i]), cr, cg, cb)

            if len(pixel_map) < 20:
                continue

            # 增强（仅用于显示，不影响坐标映射）
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            sobelx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
            sobely = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
            edges = np.sqrt(sobelx ** 2 + sobely ** 2)
            if edges.max() > 0:
                edges = (edges / edges.max() * 30).clip(0, 255).astype(np.uint8)
                for c in range(3):
                    img[:, :, c] = np.clip(img[:, :, c].astype(np.int32) + edges, 0, 255).astype(np.uint8)

            pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
            pil = ImageEnhance.Contrast(pil).enhance(1.2)
            pil = ImageEnhance.Sharpness(pil).enhance(1.5)
            img = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)

            # 翻转 Y
            img = cv2.flip(img, 0)

            # 坐标映射（Y 翻转后，用原始 RGB）
            coord_map = {}
            for (px, py), (rx, ry, rz, cr, cg, cb) in pixel_map.items():
                coord_map[f"{px},{h-1-py}"] = [rx, ry, rz, cr, cg, cb]

            fname = f"tile_{row}_{col}.png"
            cname = f"coord_tile_{row}_{col}.json"

            cv2.imwrite(str(out / fname), img, [cv2.IMWRITE_PNG_COMPRESSION, 3])
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
