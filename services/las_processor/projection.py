"""
LAS 点云多视角投影生成器

基于 panoramicPoses.csv 的位姿，在每个位置生成多方向投影：
- top: 顶视图（俯视）
- front: 前视图（平视）
- side: 侧视图（侧视）

坐标统一使用局部坐标（UTM - offset_xyz）
"""
import json
import math
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
                    ts = float(parts[0])
                    px = float(parts[2]) - offset_x
                    py = float(parts[3]) - offset_y
                    pz = float(parts[4]) - offset_z
                    qx, qy, qz, qw = float(parts[5]), float(parts[6]), float(parts[7]), float(parts[8])
                    poses.append({
                        'x': px, 'y': py, 'z': pz,
                        'qx': qx, 'qy': qy, 'qz': qz, 'qw': qw,
                        'name': parts[1],
                        'ts': ts,
                    })
    
    return poses, offset_x, offset_y, offset_z


def _quat_to_rotmat(qx, qy, qz, qw):
    """四元数转旋转矩阵"""
    return np.array([
        [1 - 2*(qy*qy + qz*qz), 2*(qx*qy - qz*qw), 2*(qx*qz + qy*qw)],
        [2*(qx*qy + qz*qw), 1 - 2*(qx*qx + qz*qz), 2*(qy*qz - qx*qw)],
        [2*(qx*qz - qy*qw), 2*(qy*qz + qx*qw), 1 - 2*(qx*qx + qy*qy)],
    ], dtype=np.float64)


def _estimate_surface_normals(points_3d, k=8):
    """基于局部点云分布近似法线，用于软投影着色。"""
    if points_3d is None or len(points_3d) < 3:
        return np.zeros((0, 3), dtype=np.float64)

    points = np.asarray(points_3d, dtype=np.float64)
    if len(points) == 3:
        return np.array([[0.0, 0.0, 1.0]] * 3, dtype=np.float64)

    centroid = points.mean(axis=0)
    centered = points - centroid
    _, _, vh = np.linalg.svd(centered, full_matrices=False)
    normals = vh[-1].reshape(1, 3)
    normals = np.repeat(normals, len(points), axis=0)
    normals = normals / (np.linalg.norm(normals, axis=1, keepdims=True) + 1e-8)
    return normals


def _get_camera_matrix(img_w, img_h, fov_deg=75):
    """据图像尺寸和视场角构造相机内参。"""
    f = max(img_w, img_h) / (2 * np.tan(np.deg2rad(fov_deg / 2)))
    return np.array([
        [f, 0, img_w / 2],
        [0, f, img_h / 2],
        [0, 0, 1],
    ], dtype=np.float64)


def _render_camera_like_points(points_3d, point_colors, camera_matrix, img_w, img_h, rvec=None, tvec=None, radius=1.2):
    """用深度缓冲、软点扩散和轻微法线着色生成更像相机视图的投影图。"""
    if points_3d is None or len(points_3d) == 0:
        return np.zeros((img_h, img_w, 3), dtype=np.uint8)

    if rvec is None:
        rvec = np.zeros(3, dtype=np.float64)
    if tvec is None:
        tvec = np.zeros(3, dtype=np.float64)

    points = np.asarray(points_3d, dtype=np.float64)
    colors = np.asarray(point_colors, dtype=np.float32)
    if colors.ndim == 1:
        colors = colors.reshape(1, 3)

    projected, _ = cv2.projectPoints(points, rvec, tvec, camera_matrix, None)
    projected = projected.reshape(-1, 2)

    img = np.zeros((img_h, img_w, 3), dtype=np.float32)
    depth_map = np.full((img_h, img_w), np.inf, dtype=np.float32)

    valid = (projected[:, 0] >= 0) & (projected[:, 0] < img_w) & (projected[:, 1] >= 0) & (projected[:, 1] < img_h) & (points[:, 2] > 1e-3)
    if not np.any(valid):
        return np.zeros((img_h, img_w, 3), dtype=np.uint8)

    valid_pts = points[valid]
    valid_proj = projected[valid]
    valid_colors = colors[valid]
    normals = _estimate_surface_normals(valid_pts)

    for idx in range(len(valid_pts)):
        px, py = int(round(valid_proj[idx, 0])), int(round(valid_proj[idx, 1]))
        if px < 0 or px >= img_w or py < 0 or py >= img_h:
            continue

        z_val = float(valid_pts[idx, 2])
        if not np.isfinite(z_val) or z_val >= depth_map[py, px]:
            continue

        depth_map[py, px] = z_val
        color = np.array([valid_colors[idx, 2], valid_colors[idx, 1], valid_colors[idx, 0]], dtype=np.float32)
        normal = normals[idx] if len(normals) > idx else np.array([0.0, 0.0, 1.0], dtype=np.float64)
        light_dir = np.array([0.2, -0.2, 1.0], dtype=np.float64)
        light_dir = light_dir / (np.linalg.norm(light_dir) + 1e-8)
        shading = 0.35 + 0.65 * np.clip(np.dot(normal, light_dir), 0.0, 1.0)
        texture_bias = 0.05 * (np.sin((px + 1) * 0.1) + np.cos((py + 1) * 0.07))
        view_factor = np.clip(1.0 - (z_val / max(abs(z_val), 1.0)), 0.6, 1.0)
        img[py, px] = color * (0.7 + texture_bias + 0.3 * shading) * view_factor

    for idx in range(len(valid_pts)):
        px, py = int(round(valid_proj[idx, 0])), int(round(valid_proj[idx, 1]))
        if px < 0 or px >= img_w or py < 0 or py >= img_h:
            continue

        z_val = float(valid_pts[idx, 2])
        if not np.isfinite(z_val) or z_val >= depth_map[py, px]:
            continue

        color = np.array([valid_colors[idx, 2], valid_colors[idx, 1], valid_colors[idx, 0]], dtype=np.float32)
        normal = normals[idx] if len(normals) > idx else np.array([0.0, 0.0, 1.0], dtype=np.float64)
        light_dir = np.array([0.2, -0.2, 1.0], dtype=np.float64)
        light_dir = light_dir / (np.linalg.norm(light_dir) + 1e-8)
        shading = 0.35 + 0.65 * np.clip(np.dot(normal, light_dir), 0.0, 1.0)
        texture_bias = 0.05 * (np.sin((px + 1) * 0.1) + np.cos((py + 1) * 0.07))
        view_factor = np.clip(1.0 - (z_val / max(abs(z_val), 1.0)), 0.6, 1.0)
        for oy in range(-int(radius), int(radius) + 1):
            for ox in range(-int(radius), int(radius) + 1):
                nx, ny = px + ox, py + oy
                if 0 <= nx < img_w and 0 <= ny < img_h:
                    dist = np.sqrt(ox * ox + oy * oy)
                    if dist <= radius:
                        weight = max(0.0, 1.0 - dist / radius)
                        if z_val < depth_map[ny, nx]:
                            img[ny, nx] = np.maximum(img[ny, nx], color * (0.7 + texture_bias + 0.3 * shading) * view_factor * weight)

    img = np.clip(img, 0, 255).astype(np.uint8)
    img = cv2.GaussianBlur(img, (3, 3), 0)
    img = cv2.convertScaleAbs(img, alpha=1.05, beta=6)
    return img


def _apply_camera_like_shading(image, depth=None):
    """对已有投影图做相机式明暗增强与对比度/饱和度提升。"""
    if image is None:
        return None

    img = np.array(image, copy=True)
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    elif img.ndim == 3 and img.shape[2] == 4:
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

    img_float = img.astype(np.float32)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    grad_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    grad_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    edge = np.sqrt(grad_x * grad_x + grad_y * grad_y)
    edge_norm = edge / (edge.max() + 1e-6)

    if depth is not None:
        depth_arr = np.asarray(depth, dtype=np.float32)
        depth_arr = np.nan_to_num(depth_arr, nan=0.0, posinf=0.0, neginf=0.0)
        depth_min = depth_arr.min() if depth_arr.size else 0.0
        depth_max = depth_arr.max() if depth_arr.size else 1.0
        depth_norm = (depth_arr - depth_min) / (depth_max - depth_min + 1e-6)
        depth_norm = np.clip(depth_norm, 0.0, 1.0)
    else:
        depth_norm = np.zeros_like(gray)

    # 边缘增强 + 深度衰减
    shading = (0.85 + 0.15 * edge_norm[..., None]) * (0.85 + 0.15 * (1.0 - depth_norm[..., None]))
    img_float = img_float * shading

    # 轻微高斯模糊去除点云噪点
    img_float = cv2.GaussianBlur(img_float, (3, 3), 0)

    # 提升对比度和亮度（关键！）
    # 由于 octree_render 使用 intensity 着色（均值约 30/255），
    # 需要较大 beta 把暗图拉到可视范围；alpha 避免过曝。
    # 同时做直方图拉伸到全动态范围，确保对比度充足。
    img_float = cv2.convertScaleAbs(img_float, alpha=1.1, beta=60)
    # 自适应直方图均衡化（CLAHE），增强局部对比度
    lab = cv2.cvtColor(img_float, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    l = clahe.apply(l)
    lab = cv2.merge([l, a, b])
    img_float = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

    # 饱和度增强（让点云颜色更鲜明）
    if img_float.shape[2] >= 3:
        hsv = cv2.cvtColor(img_float, cv2.COLOR_BGR2HSV).astype(np.float32)
        hsv[..., 1] = np.clip(hsv[..., 1] * 1.2, 0, 255)
        img_float = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)

    return img_float


def _get_view_rotation(pose, view_dir):
    """返回用于 8 个斜向地面投影方向的相机旋转矩阵。"""
    R_pose = _quat_to_rotmat(pose['qx'], pose['qy'], pose['qz'], pose['qw'])

    heading_map = {
        'n': 0.0,
        'ne': 45.0,
        'e': 90.0,
        'se': 135.0,
        's': 180.0,
        'sw': 225.0,
        'w': 270.0,
        'nw': 315.0,
    }
    if isinstance(view_dir, str):
        heading_deg = heading_map.get(view_dir.lower(), 0.0)
    else:
        heading_deg = float(view_dir)

    yaw = np.deg2rad(heading_deg)
    pitch = np.deg2rad(-35.0)
    R_yaw = np.array([
        [np.cos(yaw), -np.sin(yaw), 0.0],
        [np.sin(yaw), np.cos(yaw), 0.0],
        [0.0, 0.0, 1.0],
    ], dtype=np.float64)
    R_pitch = np.array([
        [1.0, 0.0, 0.0],
        [0.0, np.cos(pitch), -np.sin(pitch)],
        [0.0, np.sin(pitch), np.cos(pitch)],
    ], dtype=np.float64)
    return R_pitch @ R_yaw @ R_pose


def _project_points(pts_3d, pose, view_dir):
    """根据位姿和斜向地面投影方向将3D点投到2D平面。"""
    x, y, z = pts_3d[:, 0], pts_3d[:, 1], pts_3d[:, 2]

    # 平移：以位姿为中心
    x_local = x - pose['x']
    y_local = y - pose['y']
    z_local = z - pose['z']

    R_total = _get_view_rotation(pose, view_dir)

    # 相机坐标系: X右, Y下, Z前（相机朝向）
    pts_cam = R_total @ np.vstack([x_local, y_local, z_local])

    # 透视投影：图像坐标 (u, v) = (X/Z, Y/Z) * f
    depth = pts_cam[2, :]
    valid = depth > 0

    f = 100.0  # 焦距
    px = pts_cam[0, :] / np.maximum(depth, 1e-6) * f
    py = -pts_cam[1, :] / np.maximum(depth, 1e-6) * f  # Y翻转

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
    r_arr = np.array(pts.red[::step], dtype=np.uint16) if has_rgb else None
    g_arr = np.array(pts.green[::step], dtype=np.uint16) if has_rgb else None
    b_arr = np.array(pts.blue[::step], dtype=np.uint16) if has_rgb else None
    
    colors = np.zeros((len(x), 3), dtype=np.uint8)
    if r_arr is not None:
        colors[:, 0] = np.clip(r_arr >> 8, 0, 255).astype(np.uint8)
        colors[:, 1] = np.clip(g_arr >> 8, 0, 255).astype(np.uint8)
        colors[:, 2] = np.clip(b_arr >> 8, 0, 255).astype(np.uint8)
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
    view_dirs = ['n', 'ne', 'e', 'se', 's', 'sw', 'w', 'nw']
    
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
                
                # 渲染：先把点云变换到当前视角的相机坐标，再做软投影着色
                pts_view = np.column_stack([
                    pts_local[mask][valid][:, 0] - pose['x'],
                    pts_local[mask][valid][:, 1] - pose['y'],
                    pts_local[mask][valid][:, 2] - pose['z'],
                ])
                if len(pts_view) > 0:
                    R_total = _get_view_rotation(pose, vd)
                    pts_cam = (R_total @ pts_view.T).T
                    valid_cam = pts_cam[:, 2] > 0
                    pts_cam = pts_cam[valid_cam]
                    cols_local = colors_local[mask][valid]
                    cols_view = cols_local[valid_cam]
                    if len(pts_cam) > 0:
                        camera_matrix = _get_camera_matrix(w, h, fov_deg=75)
                        img = _render_camera_like_points(
                            pts_cam,
                            cols_view,
                            camera_matrix,
                            w,
                            h,
                            radius=1.2,
                        )
                    else:
                        img = np.zeros((h, w, 3), dtype=np.uint8)
                else:
                    img = np.zeros((h, w, 3), dtype=np.uint8)

                img = _apply_camera_like_shading(img)
                
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
                out_path = out / fname
                coord_path = out / cname
                out.mkdir(parents=True, exist_ok=True)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                coord_path.parent.mkdir(parents=True, exist_ok=True)
                
                cv2.imwrite(str(out_path), img, [cv2.IMWRITE_PNG_COMPRESSION, 3])
                with open(str(coord_path), "w") as f:
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
                out.mkdir(parents=True, exist_ok=True)
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
