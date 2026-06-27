import json
from pathlib import Path
from collections import defaultdict

import numpy as np
from laspy import open as las_open


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

    depth_image = np.full((height, width), -np.inf, dtype=np.float32)
    color_image = np.zeros((height, width, 3), dtype=np.uint8)

    for (px, py), pts_list in pixel_bins.items():
        zs = [p[2] for p in pts_list]
        max_z_idx = np.argmax(zs)
        depth_image[py, px] = zs[max_z_idx]
        if r is not None and g is not None and b is not None:
            color_image[py, px] = [int(r[max_z_idx]) >> 8, int(g[max_z_idx]) >> 8, int(b[max_z_idx]) >> 8]

    depth_vis = np.zeros((height, width), dtype=np.uint8)
    valid = np.isfinite(depth_image)
    if valid.any():
        d_min, d_max = depth_image[valid].min(), depth_image[valid].max()
        if d_max > d_min:
            depth_vis[valid] = (255 * (depth_image[valid] - d_min) / (d_max - d_min)).astype(np.uint8)

    depth_vis = np.flipud(depth_vis)
    color_image = np.flipud(color_image)

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    from PIL import Image
    Image.fromarray(depth_vis).save(str(out / "las_projection.jpg"))
    if color_image.max() > 0:
        Image.fromarray(color_image).save(str(out / "las_projection_rgb.jpg"))

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
