"""
LAS 点云多视角投影生成器

基于 panoramicPoses.csv 的位姿，在每个位置生成多方向投影：
- top: 顶视图（俯视）
- front: 前视图（平视）
- side: 侧视图（侧视）

坐标统一使用局部坐标（UTM - offset_xyz）
"""
import json
from pathlib import Path
from collections import defaultdict

import cv2
import numpy as np
from laspy import open as las_open
from PIL import Image, ImageEnhance


TILE_PX = 512
VIEW_RANGE = 50          # 每张图覆盖50m x 50m
RES = VIEW_RANGE / TILE_PX  # ~0.098m/像素 (10cm)


def _load_poses_and_offset(las_dir="las"):
    """加载位姿和offset"""
    map_path = Path(las_dir) / "map_config.json"
    pose_path = Path(las_dir) / "panoramicPoses.csv"
    
    offset_x, offset_y, offset_z = 0.0, 0.0, 0.0
    if map_path.exists():
        with open(map_path) as f:
            cfg = json.load(f)
        offset_x, offset_y, offset_z = cfg["offset_xyz"]
    
    poses = []
    if pose_path.exists():
        with open(pose_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split()
                if len(parts) >= 9:
                    # panoramicPoses: ts, name, x, y, z, qx, qy, qz, qw
                    # 转为局部坐标（UTM - offset）
                    px = float(parts[2]) - offset_x
                    py = float(parts[3]) - offset_y
                    pz = float(parts[4]) - offset_z
                    qx, qy, qz, qw = float(parts[5]), float(parts[6]), float(parts[7]), float(parts[8])
                    poses.append({
                        'x': px, 'y': py, 'z': pz,
                        'qx': qx, 'qy': qy, 'qz': qz, 'qw': qw,
                        'name': parts[1],
                    })
    
    return poses, offset_x, offset_y, offset_z


def _quat_to_rotmat(qx, qy, qz, qw):
    """四元数转旋转矩阵"""
    return np.array([
        [1 - 2*(qy*qy + qz*qz), 2*(qx*qy - qz*qw), 2*(qx*qz + qy*qw)],
        [2*(qx*qy + qz*qw), 1 - 2*(qx*qx + qz*qz), 2*(qy*qz - qx*qw)],
        [2*(qx*qz - qy*qw), 2*(qy*qz + qx*qw), 1 - 2*(qx*qx + qy*qy)],
    ], dtype=np.float64)


def _project_points(pts_3d, pose, view_dir):
    """
    根据位姿和视角方向投影3D点。
    view_dir: 'top', 'front', 'side'
    """
    x, y, z = pts_3d[:, 0], pts_3d[:, 1], pts_3d[:, 2]
    
    # 平移：以位姿为中心
    x_local = x - pose['x']
    y_local = y - pose['y']
    z_local = z - pose['z']
    
    # 旋转矩阵（世界→相机）
    R = _quat_to_rotmat(pose['qx'], pose['qy'], pose['qz'], pose['qw'])
    
    # 根据视角方向调整
    if view_dir == 'top':
        # 顶视图：俯视（绕X轴转-90度）
        R_view = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], dtype=np.float64)
    elif view_dir == 'front':
        # 前视图：平视
        R_view = np.eye(3, dtype=np.float64)
    elif view_dir == 'side':
        # 侧视图：绕Z轴转90度
        R_view = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]], dtype=np.float64)
    else:
        R_view = np.eye(3, dtype=np.float64)
    
    R_total = R_view @ R
    
    # 投影
    pts_cam = R_total @ np.vstack([x_local, y_local, z_local])
    
    # 透视投影（忽略Z，只取XY作为图像坐标）
    px = pts_cam[1, :]  # 图像X
    py = -pts_cam[2, :]  # 图像Y（翻转）
    depth = pts_cam[0, :]  # 深度（Z朝向）
    
    return px, py, depth


def _render_projection(px, py, colors, w, h, range_m):
    """渲染投影图像"""
    img = np.zeros((h, w, 3), dtype=np.uint8)
    
    # 将3D坐标映射到像素
    half = range_m / 2
    col = ((px + half) / range_m * w).astype(np.int64)
    row = ((py + half) / range_m * h).astype(np.int64)
    
    valid = (col >= 0) & (col < w) & (row >= 0) & (row < h)
    
    for i in range(len(col)):
        if not valid[i]:
            continue
        c, r = int(col[i]), int(row[i])
        cr, cg, cb = colors[i]
        img[r, c, 0] = np.clip(int(cb), 0, 255)
        img[r, c, 1] = np.clip(int(cg), 0, 255)
        img[r, c, 2] = np.clip(int(cr), 0, 255)
    
    # 增强
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
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


def _build_coord_map(px, py, depth, colors, col_idx, row_idx, w, h):
    """构建像素→3D坐标映射（取Z最近的点的坐标）"""
    coord_map = {}
    for i in range(len(col_idx)):
        c, r = int(col_idx[i]), int(row_idx[i])
        if c < 0 or c >= w or r < 0 or r >= h:
            continue
        key = f"{c},{r}"
        # 保留深度最近（Z最小）的点
        if key not in coord_map or depth[i] < coord_map[key][3]:
            coord_map[key] = [float(px[i]), float(py[i]), float(depth[i]),
                             int(colors[i][0]), int(colors[i][1]), int(colors[i][2])]
    
    # 翻转Y
    flipped = {}
    for key, val in coord_map.items():
        x_str, y_str = key.split(',')
        flipped[f"{x_str},{h-1-int(y_str)}"] = val
    return flipped


def project_las_multi_view(
    las_path: str,
    output_dir: str = "projections",
    max_poses: int = 50,
):
    """
    基于位姿的多视角投影。
    每个位姿生成3张图（top/front/side），每张512x512覆盖50x50m。
    """
    las_dir = str(Path(las_path).parent)
    poses, offset_x, offset_y, offset_z = _load_poses_and_offset(las_dir)
    
    if not poses:
        # 无位姿时用网格投影
        poses = None
    
    reader = las_open(las_path)
    pts = reader.read()
    total = len(pts.x)
    
    # 采样
    step = max(1, total // 3_000_000)
    x = np.array(pts.x[::step], dtype=np.float64) - offset_x
    y = np.array(pts.y[::step], dtype=np.float64) - offset_y
    z = np.array(pts.z[::step], dtype=np.float64) - offset_z
    
    has_rgb = hasattr(pts, 'red') and hasattr(pts, 'green') and hasattr(pts, 'blue')
    r_arr = np.array(pts.red[::step], dtype=np.uint8) if has_rgb else None
    g_arr = np.array(pts.green[::step], dtype=np.uint8) if has_rgb else None
    b_arr = np.array(pts.blue[::step], dtype=np.uint8) if has_rgb else None
    
    colors = np.zeros((len(x), 3), dtype=np.uint8)
    if r_arr is not None:
        colors[:, 0] = np.clip(r_arr, 0, 255)
        colors[:, 1] = np.clip(g_arr, 0, 255)
        colors[:, 2] = np.clip(b_arr, 0, 255)
    else:
        colors[:, 0] = 128
        colors[:, 1] = 128
        colors[:, 2] = 128
    
    pts_3d = np.column_stack([x, y, z])
    
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    
    # 清理旧文件
    for pattern in ["view_*.png", "coord_view_*.json", "tile_*.png", "coord_tile_*.json"]:
        for f in out.glob(pattern):
            f.unlink(missing_ok=True)
    
    generated = []
    view_dirs = ['top', 'front', 'side']
    
    if poses:
        # 从位姿投影
        selected_poses = poses[:min(max_poses, len(poses))]
        for pi, pose in enumerate(selected_poses):
            # 先按空间范围裁剪点云（只取位姿附近50m内的点）
            half = VIEW_RANGE / 2
            spatial_mask = (
                (pts_3d[:, 0] >= pose['x'] - half) & (pts_3d[:, 0] < pose['x'] + half) &
                (pts_3d[:, 1] >= pose['y'] - half) & (pts_3d[:, 1] < pose['y'] + half)
            )
            pts_local = pts_3d[spatial_mask]
            colors_local = colors[spatial_mask]
            
            if len(pts_local) < 30:
                continue
            
            for vd in view_dirs:
                px, py, depth = _project_points(pts_local, pose, vd)
                
                # 筛选在范围内的点
                mask = (px >= -half) & (px < half) & (py >= -half) & (py < half)
                
                if mask.sum() < 50:
                    continue
                
                w = h = TILE_PX
                col = ((px[mask] + half) / VIEW_RANGE * w).astype(np.int64)
                row = ((py[mask] + half) / VIEW_RANGE * h).astype(np.int64)
                
                valid = (col >= 0) & (col < w) & (row >= 0) & (row < h)
                col, row = col[valid], row[valid]
                
                if len(col) < 30:
                    continue
                
                # 渲染
                img = np.zeros((h, w, 3), dtype=np.uint8)
                pts_mask = pts_local[mask][valid]
                cols_local = colors_local[mask][valid]
                for i in range(len(col)):
                    cr, cg, cb = cols_local[i]
                    img[row[i], col[i]] = [int(cb), int(cg), int(cr)]
                
                # 增强
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                s = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
                sy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
                edges = np.sqrt(s ** 2 + sy ** 2)
                if edges.max() > 0:
                    edges = (edges / edges.max() * 30).clip(0, 255).astype(np.uint8)
                    for c in range(3):
                        img[:, :, c] = np.clip(img[:, :, c].astype(np.int32) + edges, 0, 255).astype(np.uint8)
                
                pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
                pil = ImageEnhance.Contrast(pil).enhance(1.2)
                pil = ImageEnhance.Sharpness(pil).enhance(1.5)
                img = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
                
                # 坐标映射
                coord_map = {}
                depth_local = depth[mask][valid]
                px_local = px[mask][valid]
                py_local = py[mask][valid]
                for i in range(len(col)):
                    key = f"{col[i]},{h-1-row[i]}"
                    if key not in coord_map or depth_local[i] < coord_map[key][3]:
                        cr, cg, cb = cols_local[i]
                        coord_map[key] = [float(px_local[i]), float(py_local[i]),
                                         float(depth_local[i]), int(cr), int(cg), int(cb)]
                
                fx = f"{pose['x']:.1f}_{pose['y']:.1f}"
                fname = f"view_{vd}_{fx}_{pi}.png"
                cname = f"coord_{vd}_{fx}_{pi}.json"
                
                cv2.imwrite(str(out / fname), img, [cv2.IMWRITE_PNG_COMPRESSION, 3])
                with open(str(out / cname), "w") as f:
                    json.dump({
                        "width": w, "height": h,
                        "resolution": RES,
                        "view": vd,
                        "pose_x": pose['x'], "pose_y": pose['y'], "pose_z": pose['z'],
                        "pixel_count": len(coord_map),
                        "pixels": coord_map,
                    }, f, separators=(",", ":"))
                
                generated.append({
                    "image_path": str(out / fname),
                    "coord_map_path": str(out / cname),
                    "width": w, "height": h,
                    "view": vd,
                    "tile": f"{pose['x']:.1f}_{pose['y']:.1f}",
                    "pixel_count": len(coord_map),
                })
    else:
        # 无位姿时用网格投影（降级方案）
        x_min, x_max = float(x.min()), float(x.max())
        y_min, y_max = float(y.min()), float(y.max())
        tile_m = VIEW_RANGE
        n_cols = max(1, int(np.ceil((x_max - x_min) / tile_m)))
        n_rows = max(1, int(np.ceil((y_max - y_min) / tile_m)))
        
        for row in range(n_rows):
            for col in range(n_cols):
                tx_min = x_min + col * tile_m
                tx_max = min(x_min + (col + 1) * tile_m, x_max)
                ty_min = y_min + row * tile_m
                ty_max = min(y_min + (row + 1) * tile_m, y_max)
                
                mask = (x >= tx_min) & (x < tx_max) & (y >= ty_min) & (y < ty_max)
                if mask.sum() < 30:
                    continue
                
                w = h = TILE_PX
                col_idx = ((x[mask] - tx_min) / RES).astype(np.int64).clip(0, w - 1)
                row_idx = ((y[mask] - ty_min) / RES).astype(np.int64).clip(0, h - 1)
                
                img = np.zeros((h, w, 3), dtype=np.uint8)
                coord_map = {}
                for i in range(len(col_idx)):
                    px_i, py_i = int(col_idx[i]), int(row_idx[i])
                    cr, cg, cb = colors[mask][i]
                    img[py_i, px_i] = [int(cb), int(cg), int(cr)]
                    key = f"{px_i},{h-1-py_i}"
                    if key not in coord_map or z[mask][i] < coord_map[key][2]:
                        coord_map[key] = [float(x[mask][i]), float(y[mask][i]), float(z[mask][i]),
                                         int(cr), int(cg), int(cb)]
                
                fname = f"tile_{row}_{col}.png"
                cname = f"coord_tile_{row}_{col}.json"
                cv2.imwrite(str(out / fname), img)
                with open(str(out / cname), "w") as f:
                    json.dump({"width": w, "height": h, "pixel_count": len(coord_map), "pixels": coord_map}, f)
                generated.append({
                    "image_path": str(out / fname), "coord_map_path": str(out / cname),
                    "width": w, "height": h, "tile": f"{row}_{col}", "pixel_count": len(coord_map),
                })
    
    # 保存索引
    with open(str(out / "tile_index.json"), "w") as f:
        json.dump(generated, f, indent=2)
    
    return generated


project_las_to_image = project_las_multi_view
