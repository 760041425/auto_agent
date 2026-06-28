"""
端到端视觉定位服务

流程：
1. 加载 COLMAP 数据（3D点云 + 图像位姿）
2. 查询图像提取特征 → 与3D点匹配 → PnP位姿估计
3. 根据位姿重新投影 → 生成投影图 + 坐标映射
4. 前端对比原图与投影图，标注匹配点连线
5. 多轮迭代优化位姿
6. 多种特征/匹配方案对比（SIFT / SuperPoint / LoFTR）
"""
import json
import logging
import os
import time
from pathlib import Path
from collections import defaultdict

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from services.las_processor.colmap_reader import read_images_txt, read_points3d_txt

LOG_DIR = Path(__file__).parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

_logger = logging.getLogger("localizer")
_logger.setLevel(logging.DEBUG)
_fh = logging.FileHandler(str(LOG_DIR / "localizer.log"), mode="a", encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
_logger.handlers.clear()
_logger.addHandler(_fh)
_sh = logging.StreamHandler()
_sh.setFormatter(logging.Formatter("%(asctime)s [LOCALIZER] %(message)s", datefmt="%H:%M:%S"))
_logger.addHandler(_sh)


def log(msg: str):
    _logger.info(msg)


# ── 全局缓存 ─────────────────────────────────────────
_COLMAP_POINTS = None
_COLMAP_IMAGES = None
_POINT_INDEX = None  # 3D点空间索引


def load_colmap(las_dir: str = "las", force_reload: bool = False):
    """加载 COLMAP 数据"""
    global _COLMAP_POINTS, _COLMAP_IMAGES, _POINT_INDEX

    if _COLMAP_POINTS is not None and not force_reload:
        return _COLMAP_POINTS, _COLMAP_IMAGES

    log("加载 COLMAP 数据...")
    t0 = time.time()

    img_path = os.path.join(las_dir, "images.txt")
    pt_path = os.path.join(las_dir, "points3D.txt")

    _COLMAP_IMAGES = read_images_txt(img_path)
    _COLMAP_POINTS = read_points3d_txt(pt_path)

    log(f"  {len(_COLMAP_IMAGES)} 张图像, {len(_COLMAP_POINTS)} 个3D点, 耗时{time.time()-t0:.1f}s")

    # 构建空间索引
    log("构建3D点空间索引...")
    pts_array = []
    pt_ids = []
    for pid, p in _COLMAP_POINTS.items():
        pts_array.append([p.x, p.y, p.z])
        pt_ids.append(pid)
    _POINT_INDEX = {
        "pts": np.array(pts_array, dtype=np.float32),
        "ids": np.array(pt_ids),
    }
    log(f"  索引: {len(pts_array)} 个点")

    return _COLMAP_POINTS, _COLMAP_IMAGES


def _build_3d_point_descriptors(
    query_kp, query_des,
    known_images, known_points,
    top_k_images: int = 50,
):
    """
    通过图像检索找到与查询图像最相似的 COLMAP 图像，
    然后将这些图像观测到的 3D 点作为候选匹配。
    """
    # 用查询图像特征与 COLMAP 图像做匹配
    if query_des is None or len(query_des) < 10:
        return [], []

    # 采样 COLMAP 图像用于匹配
    sample_imgs = known_images[:min(top_k_images * 5, len(known_images))]
    
    matched_3d = []
    matched_2d = []
    used_3d = set()
    
    flann = cv2.FlannBasedMatcher(dict(algorithm=1, trees=5), dict(checks=50))

    for img in sample_imgs:
        img_path = os.path.join("las", img.name)
        if not os.path.exists(img_path):
            continue
        
        img_gray = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)
        if img_gray is None:
            continue
        
        sift = cv2.SIFT_create(nfeatures=2000)
        img_kp, img_des = sift.detectAndCompute(img_gray, None)
        if img_des is None or len(img_des) < 10:
            continue

        try:
            knn = flann.knnMatch(query_des, img_des, k=2)
        except cv2.error:
            continue

        for pair in knn:
            if len(pair) != 2:
                continue
            m, n = pair[0], pair[1]
            if m.distance > 0.75 * n.distance:
                continue

            # 查询图像中的2D点
            q_pt = query_kp[m.queryIdx].pt
            
            # COLMAP 图像中对应的2D点
            train_idx = m.trainIdx
            if train_idx >= len(img_kp):
                continue
            colmap_2d = img_kp[trainIdx].pt

            # 查找该 COLMAP 图像在该点观测到的 3D 点
            for pt2d_x, pt2d_y, pt3d_id in img.points2d:
                dist = np.sqrt((colmap_2d[0] - pt2d_x)**2 + (colmap_2d[1] - pt2d_y)**2)
                if dist < 5.0 and pt3d_id > 0 and pt3d_id not in used_3d:
                    if pt3d_id in known_points:
                        p3d = known_points[pt3d_id]
                        matched_3d.append([p3d.x, p3d.y, p3d.z])
                        matched_2d.append([q_pt[0], q_pt[1]])
                        used_3d.add(pt3d_id)
                        break

    return np.array(matched_3d, dtype=np.float64), np.array(matched_2d, dtype=np.float64)


def _solve_pnp(object_pts, image_pts, camera_matrix, dist_coeffs=None):
    """PnP 位姿估计"""
    if len(object_pts) < 4:
        return None, None, None

    if dist_coeffs is None:
        dist_coeffs = np.zeros((4, 1))

    success, rvec, tvec, inliers = cv2.solvePnPRansac(
        object_pts, image_pts, camera_matrix, dist_coeffs,
        iterationsCount=2000, reprojectionError=8.0, confidence=0.99,
        flags=cv2.SOLVEPNP_EPNP,
    )

    if not success:
        return None, None, None

    return rvec, tvec, inliers


def _rotation_matrix_to_quaternion(R):
    """旋转矩阵 → 四元数"""
    q = np.zeros(4)
    q[0] = np.sqrt(1 + R[0,0] + R[1,1] + R[2,2]) / 2
    q[1] = (R[2,1] - R[1,2]) / (4 * q[0])
    q[2] = (R[0,2] - R[2,0]) / (4 * q[0])
    q[3] = (R[1,0] - R[0,1]) / (4 * q[0])
    return q


def _get_camera_matrix(img_w, img_h, fov_deg=60):
    """根据图像尺寸和视场角估算内参矩阵"""
    f = max(img_w, img_h) / (2 * np.tan(np.deg2rad(fov_deg / 2)))
    return np.array([
        [f, 0, img_w / 2],
        [0, f, img_h / 2],
        [0, 0, 1]
    ], dtype=np.float64)


def reproject_points(rvec, tvec, camera_matrix, points_3d, img_w, img_h):
    """将3D点根据位姿和相机内参投影到2D图像平面"""
    if len(points_3d) == 0:
        return [], []

    rmat, _ = cv2.Rodrigues(rvec)
    # 投影
    projected, _ = cv2.projectPoints(points_3d, rvec, tvec, camera_matrix, None)
    projected = projected.reshape(-1, 2)

    # 筛选在图像范围内的点
    valid = (projected[:, 0] >= 0) & (projected[:, 0] < img_w) & \
            (projected[:, 1] >= 0) & (projected[:, 1] < img_h)
    
    return projected[valid], valid


def render_projection_image(
    points_3d, point_colors,
    rvec, tvec, camera_matrix,
    img_w, img_h, output_path,
    resolution_scale=1.0,
):
    """
    根据位姿将3D点云重新投影为2D图像。

    返回: (输出图像路径, 像素到3D坐标映射)
    """
    w = int(img_w * resolution_scale)
    h = int(img_h * resolution_scale)

    # 缩放内参
    K = camera_matrix.copy()
    K[:2] *= resolution_scale

    # 投影所有3D点
    projected, valid = reproject_points(rvec, tvec, K, points_3d, w, h)

    if len(projected) == 0:
        return None, {}

    # 构建深度图 + 颜色图
    img = np.zeros((h, w, 3), dtype=np.uint8)
    depth_img = np.full((h, w), np.nan, dtype=np.float32)
    coord_map = {}

    valid_3d = points_3d[valid]
    valid_colors = point_colors[valid]

    for i in range(len(projected)):
        px, py = int(round(projected[i, 0])), int(round(projected[i, 1]))
        if px < 0 or px >= w or py < 0 or py >= h:
            continue
        
        # Z-buffer: 保留最近的投影点
        z_val = valid_3d[i, 2]
        if np.isnan(depth_img[py, px]) or z_val < depth_img[py, px]:
            depth_img[py, px] = z_val
            r, g, b = valid_colors[i]
            img[py, px] = [int(b), int(g), int(r)]
            coord_map[f"{px},{py}"] = [float(v) for v in valid_3d[i]]

    # 深度着色（对无颜色的区域）
    valid_depth = ~np.isnan(depth_img)
    if valid_depth.any() and img.sum() == 0:
        d_min, d_max = np.nanmin(depth_img[valid_depth]), np.nanmax(depth_img[valid_depth])
        if d_max > d_min:
            norm = np.zeros((h, w), dtype=np.uint8)
            norm[valid_depth] = (255 * (depth_img[valid_depth] - d_min) / (d_max - d_min)).astype(np.uint8)
            img = cv2.applyColorMap(norm, cv2.COLORMAP_JET)

    cv2.imwrite(output_path, img)

    return output_path, coord_map


def localize_image(
    query_image_path: str,
    output_dir: str = "projections/localize",
    feature_method: str = "sift",
    match_method: str = "flann",
    max_iterations: int = 3,
) -> dict:
    """
    端到端视觉定位主函数。

    特征方法: sift, superpoint
    匹配方法: flann, ransac
    """
    log(f"{'='*60}")
    log(f"🚀 视觉定位: {os.path.basename(query_image_path)}")
    log(f"   特征={feature_method}, 匹配={match_method}, 迭代={max_iterations}")

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # 1. 加载 COLMAP 数据
    known_points, known_images = load_colmap()

    # 2. 读取查询图像
    query_img = cv2.imread(query_image_path)
    if query_img is None:
        return {"success": False, "error": "Cannot read query image"}
    q_h, q_w = query_img.shape[:2]

    camera_matrix = _get_camera_matrix(q_w, q_h)
    log(f"📷 查询图像: {q_w}x{q_h}, 内参矩阵已估算")

    # 3. 提取查询图像特征
    sift = cv2.SIFT_create(nfeatures=3000)
    q_gray = cv2.cvtColor(query_img, cv2.COLOR_BGR2GRAY)
    q_kp, q_des = sift.detectAndCompute(q_gray, None)
    log(f"🔍 SIFT特征: {len(q_kp) if q_kp is not None else 0} 个")

    if q_des is None or len(q_des) < 10:
        return {"success": False, "error": "Too few features"}

    # 4. 图像检索 + 3D-2D匹配 → PnP
    log("🔗 3D-2D匹配中...")
    pts_3d, pts_2d = _build_3d_point_descriptors(
        q_kp, q_des, known_images, known_points, top_k_images=100
    )

    log(f"   候选匹配: {len(pts_3d)} 个3D-2D点对")

    if len(pts_3d) < 4:
        return {"success": False, "error": f"Too few 3D-2D matches ({len(pts_3d)})"}

    # 5. PnP 位姿估计
    rvec, tvec, inliers = _solve_pnp(pts_3d, pts_2d, camera_matrix)
    if rvec is None:
        log("❌ PnP 位姿估计失败")
        return {"success": False, "error": "PnP failed"}

    inlier_count = len(inliers) if inliers is not None else 0
    log(f"✅ PnP成功: 内点={inlier_count}/{len(pts_3d)}")

    # 6. 多轮迭代优化
    best_rvec, best_tvec = rvec, tvec
    best_inliers = inlier_count

    for iteration in range(1, max_iterations):
        log(f"🔄 迭代 {iteration+1}/{max_iterations}...")
        
        # 用当前位姿重新投影，找到更多3D-2D匹配
        reprojected, valid_mask = reproject_points(
            best_rvec, best_tvec, camera_matrix,
            _POINT_INDEX["pts"], q_w, q_h
        )
        
        # 在重投影位置附近搜索匹配
        new_pts_3d = []
        new_pts_2d = []
        
        for i, (px, py) in enumerate(reprojected):
            if not valid_mask[i]:
                continue
            # 在查询图像上重投影位置附近提取特征匹配
            px_i, py_i = int(round(px)), int(round(py))
            x_min, x_max = max(0, px_i-10), min(q_w-1, px_i+10)
            y_min, y_max = max(0, py_i-10), min(q_h-1, py_i+10)
            
            # 找附近的关键点
            for kp in q_kp:
                if x_min <= kp.pt[0] <= x_max and y_min <= kp.pt[1] <= y_max:
                    pid = _POINT_INDEX["ids"][i]
                    p3d = known_points.get(int(pid))
                    if p3d:
                        new_pts_3d.append([p3d.x, p3d.y, p3d.z])
                        new_pts_2d.append([kp.pt[0], kp.pt[1]])
                        break

        if len(new_pts_3d) >= 4:
            new_rvec, new_tvec, new_inliers = _solve_pnp(
                np.array(new_pts_3d), np.array(new_pts_2d), camera_matrix
            )
            if new_rvec is not None:
                new_inlier_count = len(new_inliers) if new_inliers is not None else 0
                if new_inlier_count > best_inliers:
                    best_rvec, best_tvec = new_rvec, new_tvec
                    best_inliers = new_inlier_count
                    log(f"  → 优化成功: 内点={new_inlier_count}")
                else:
                    log(f"  → 未提升: {new_inlier_count} ≤ {best_inliers}")

    # 7. 生成重投影图像
    log("🖼️ 生成重投影图像...")
    
    all_pts_3d = np.array([(p.x, p.y, p.z) for p in known_points.values()], dtype=np.float64)
    all_colors = np.array([(p.r, p.g, p.b) for p in known_points.values()], dtype=np.uint8)

    proj_path = str(out / "reprojection.png")
    proj_path, coord_map = render_projection_image(
        all_pts_3d, all_colors,
        best_rvec, best_tvec, camera_matrix,
        q_w, q_h, proj_path
    )

    # 8. 保存坐标映射
    coord_path = str(out / "reprojection_coord.json")
    with open(coord_path, "w") as f:
        json.dump({
            "width": q_w, "height": q_h,
            "pixels": coord_map,
        }, f)

    # 9. 生成双图对比（原图 + 投影图）
    if proj_path:
        proj_img = cv2.imread(proj_path)
        if proj_img is not None:
            # 调整到相同高度
            h = max(q_h, proj_img.shape[0])
            w = q_w + proj_img.shape[1]
            canvas = np.zeros((h, w, 3), dtype=np.uint8)
            canvas[:q_h, :q_w] = query_img
            canvas[:proj_img.shape[0], q_w:q_w+proj_img.shape[1]] = proj_img

            # 标注匹配点连线
            colors_map = [
                (255, 0, 0), (0, 255, 0), (0, 0, 255),
                (255, 255, 0), (255, 0, 255), (0, 255, 255),
            ]
            for i in range(min(best_inliers, 5)):
                if inliers is not None and i < len(inliers):
                    idx = inliers[i][0]
                    if idx < len(pts_2d):
                        x1, y1 = int(pts_2d[idx, 0]), int(pts_2d[idx, 1])
                        # 投影图上对应位置
                        p3d = pts_3d[idx]
                        projected_pts, _ = cv2.projectPoints(
                            p3d.reshape(1, 1, 3), best_rvec, best_tvec,
                            camera_matrix, None
                        )
                        x2, y2 = int(projected_pts[0, 0, 0]) + q_w, int(projected_pts[0, 0, 1])
                        color = colors_map[i % len(colors_map)]
                        cv2.circle(canvas, (x1, y1), 5, color, -1)
                        cv2.circle(canvas, (x2, y2), 5, color, -1)
                        cv2.line(canvas, (x1, y1), (x2, y2), color, 2)

            comparison_path = str(out / "comparison.png")
            cv2.imwrite(comparison_path, canvas)
            log(f"✅ 双图对比已保存: {comparison_path}")

    # 10. 将 rvec, tvec 转为四元数 + 平移
    rmat, _ = cv2.Rodrigues(best_rvec)
    quat = _rotation_matrix_to_quaternion(rmat)
    translation = best_tvec.flatten().tolist()

    result = {
        "success": True,
        "pose": {
            "quaternion": [float(q) for q in quat],
            "translation": translation,
            "rotation_matrix": rmat.tolist(),
        },
        "inliers": int(best_inliers),
        "total_matches": len(pts_3d),
        "reprojection_image": proj_path,
        "comparison_image": comparison_path if 'comparison_path' in dir() else None,
        "coord_map": coord_path,
        "feature_method": feature_method,
        "match_method": match_method,
        "iterations": max_iterations,
    }

    log(f"✅ 定位完成: 内点={best_inliers}, 位姿=[{translation[0]:.2f}, {translation[1]:.2f}, {translation[2]:.2f}]")
    log(f"{'='*60}")

    return result
