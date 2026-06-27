import json
from pathlib import Path

import numpy as np
from laspy import open as las_open


def project_las_to_image(
    las_path: str,
    output_dir: str = "projections",
    resolution: float = 0.05,
    max_points: int | None = None,
) -> dict:
    reader = las_open(las_path)
    pts = reader.read()
    if max_points and len(pts.x) > max_points:
        step = len(pts.x) // max_points + 1
        x = pts.x[::step]
        y = pts.y[::step]
        z = pts.z[::step]
    else:
        x, y, z = pts.x, pts.y, pts.z

    x_min, x_max = x.min(), x.max()
    y_min, y_max = y.min(), y.max()

    width = int((x_max - x_min) / resolution) + 1
    height = int((y_max - y_min) / resolution) + 1

    col = ((x - x_min) / resolution).astype(np.int64).clip(0, width - 1)
    row = ((y - y_min) / resolution).astype(np.int64).clip(0, height - 1)

    depth_image = np.full((height, width), -np.inf, dtype=np.float32)
    coord_image = np.full((height, width, 3), np.nan, dtype=np.float32)

    z_values = z
    n = len(x)
    for i in range(n):
        r = row[i]
        c = col[i]
        if z_values[i] > depth_image[r, c]:
            depth_image[r, c] = z_values[i]
            coord_image[r, c] = [x[i], y[i], z[i]]

    depth_vis = np.zeros((height, width), dtype=np.uint8)
    valid = np.isfinite(depth_image)
    if valid.any():
        d_min, d_max = depth_image[valid].min(), depth_image[valid].max()
        depth_vis[valid] = (255 * (depth_image[valid] - d_min) / (d_max - d_min + 1e-8)).astype(np.uint8)

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    depth_vis = np.flipud(depth_vis)
    coord_image = np.flipud(coord_image)

    from PIL import Image
    Image.fromarray(depth_vis).save(str(out / "las_projection.jpg"))

    coord_map = {}
    for r in range(coord_image.shape[0]):
        for c in range(coord_image.shape[1]):
            coords = coord_image[r, c]
            if not np.isnan(coords[0]):
                coord_map[str((c, r))] = [float(coords[0]), float(coords[1]), float(coords[2])]

    coord_path = out / "coord_map.json"
    with open(coord_path, "w") as f:
        json.dump({"width": width, "height": height, "resolution": resolution,
                    "x_min": float(x_min), "y_min": float(y_min),
                     "x_max": float(x_max), "y_max": float(y_max),
                     "pixels": coord_map}, f)

    return {"image_path": str(out / "las_projection.jpg"),
            "coord_map_path": str(coord_path),
            "width": width, "height": height}
