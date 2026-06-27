import json
from pathlib import Path
from collections import defaultdict

import cv2
import numpy as np
from laspy import open as las_open
from PIL import Image, ImageEnhance


def _make_texture_image(height, width, pixel_bins, r, g, b):
    """生成带纹理信息的彩色投影图，增强 SIFT 可匹配性"""
    # 1. 用 RGB 颜色渲染（如果有的话）
    rgb_image = np.zeros((height, width, 3), dtype=np.uint8)
    # 2. 用高度着色
    depth_image = np.full((height, width), -np.inf, dtype=np.float32)
    # 3. 用强度（intensity）着色
    intensity_image = np.full((height, width), -np.inf, dtype=np.float32)

    has_rgb = r is not None and g is not None and b is not None

    for (px, py), pts_list in pixel_bins.items():
        # 取每个像素中 Z 最大的点作为代表
        zs = [p[2] for p in pts_list]
        max_z_idx = np.argmax(zs)
        depth_image[py, px] = zs[max_z_idx]

        if has_rgb:
            rgb_image[py, px] = [int(r[max_z_idx]) >> 8, int(g[max_z_idx]) >> 8, int(b[max_z_idx]) >> 8]

    # 深度图归一化
    depth_vis = np.zeros((height, width), dtype=np.uint8)
    valid_depth = np.isfinite(depth_image)
    if valid_depth.any():
        d_min, d_max = depth_image[valid_depth].min(), depth_image[valid_depth].max()
        if d_max > d_min:
            norm = (255 * (depth_image[valid_depth] - d_min) / (d_max - d_min)).astype(np.uint8)
            depth_vis[valid_depth] = norm

    # 翻转 Y 轴
    depth_vis = np.flipud(depth_vis)
    rgb_image = np.flipud(rgb_image)

    # 合成最终图像
    if has_rgb and rgb_image.max() > 10:
        # 有 RGB 就用 RGB
        final = rgb_image
        # 增强对比度
        pil_img = Image.fromarray(final)
        enhancer = ImageEnhance.Contrast(pil_img)
        final = np.array(enhancer.enhance(1.5))
        enhancer2 = ImageEnhance.Sharpness(Image.fromarray(final))
        final = np.array(enhancer2.enhance(2.0))
    else:
        # 无 RGB：用深度图，但做伪彩色增强 + 边缘增强
        depth_colored = np.stack([depth_vis] * 3, axis=-1)
        pil_img = Image.fromarray(depth_colored)
        # 强对比度
        enhancer = ImageEnhance.Contrast(pil_img)
        pil_img = enhancer.enhance(2.0)
        enhancer2 = ImageEnhance.Sharpness(pil_img)
        pil_img = enhancer2.enhance(3.0)
        final = np.array(pil_img)

    # 边缘增强：Sobel 梯度叠加
    gray = cv2.cvtColor(final, cv2.COLOR_RGB2GRAY) if final.shape[2] == 3 else final
    sobelx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    sobely = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    edges = np.sqrt(sobelx ** 2 + sobely ** 2)
    edges = (edges / edges.max() * 60).clip(0, 255).astype(np.uint8)
    # 叠加边缘到图像
    if final.shape[2] == 3:
        for c in range(3):
            final[:, :, c] = np.clip(final[:, :, c].astype(np.int32) + edges, 0, 255).astype(np.uint8)
    else:
        final = np.clip(final.astype(np.int32) + edges, 0, 255).astype(np.uint8)
        final = np.stack([final] * 3, axis=-1)

    return final, depth_vis


def project_las_to_image(
    las_path: str,
    output_dir: str = "projections",
    resolution: float = 0.1,
    max_points: int | None = None,
) -> dict:
    reader = las_open(las_path)
    pts = reader.read()
    total = len(pts.x)

    if max_points and total > max_points:
        step = total // max_points + 1
        x = pts.x[::step]
        y = pts.y[::step]
        z = pts.z[::step]
        if hasattr(pts, 'red'):
            r, g, b = pts.red[::step], pts.green[::step], pts.blue[::step]
        else:
            r = g = b = None
    else:
        x, y, z = pts.x, pts.y, pts.z
        if hasattr(pts, 'red'):
            r, g, b = pts.red, pts.green, pts.blue
        else:
            r = g = b = None

    x_min, x_max = x.min(), x.max()
    y_min, y_max = y.min(), y.max()

    width = int((x_max - x_min) / resolution) + 1
    height = int((y_max - y_min) / resolution) + 1

    col = ((x - x_min) / resolution).astype(np.int64).clip(0, width - 1)
    row = ((y - y_min) / resolution).astype(np.int64).clip(0, height - 1)

    pixel_bins = defaultdict(list)
    for i in range(len(x)):
        pixel_bins[(col[i], row[i])].append((float(x[i]), float(y[i]), float(z[i])))

    # 生成纹理增强的投影图
    final_image, _ = _make_texture_image(height, width, pixel_bins, r, g, b)

    # 保存
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    Image.fromarray(final_image).save(str(out / "las_projection.jpg"))

    # 坐标映射（用原始数据）
    coord_map = {}
    for (px, py), pts_list in pixel_bins.items():
        fy = height - 1 - py
        center = np.mean(pts_list, axis=0).tolist()
        coord_map[f"{px},{fy}"] = center

    coord_path = out / "coord_map.json"
    with open(coord_path, "w") as f:
        json.dump({
            "width": width, "height": height,
            "resolution": resolution,
            "x_min": float(x_min), "y_min": float(y_min),
            "x_max": float(x_max), "y_max": float(y_max),
            "pixel_count": len(coord_map),
            "pixels": coord_map,
        }, f)

    return {
        "image_path": str(out / "las_projection.jpg"),
        "coord_map_path": str(coord_path),
        "width": width, "height": height,
        "pixel_count": len(coord_map),
    }
