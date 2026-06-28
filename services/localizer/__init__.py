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


def _extract_features(image, method="sift"):
    """按指定方法提取特征"""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image

    if method == "sift":
        detector = cv2.SIFT_create(nfeatures=3000)
        return detector.detectAndCompute(gray, None)
    elif method == "orb":
        detector = cv2.ORB_create(nfeatures=3000, scaleFactor=1.2, nlevels=8)
        kp, des = detector.detectAndCompute(gray, None)
        if des is not None:
            des = des.astype(np.uint8)
        return kp, des
    elif method == "akaze":
        detector = cv2.AKAZE_create()
        return detector.detectAndCompute(gray, None)
    else:
        detector = cv2.SIFT_create(nfeatures=3000)
        return detector.detectAndCompute(gray, None)


def _match_features(des1, des2, method="flann", ratio=0.75):
    """按指定方法匹配特征

    方法:
      flann      - FLANN kd-tree + Lowe 0.75 比率测试（默认）
      bf         - BruteForce + Lowe 0.75 比率测试
      flann_lowes - FLANN + 严格 Lowe 0.6 比率测试
      bf_cross   - BruteForce + 交叉验证（双向匹配）
      knn_rank   - FLANN + 取前 N 个最近邻（无比率测试，取 top-50）
    """
    if des1 is None or des2 is None or len(des1) < 2 or len(des2) < 2:
        return []

    is_binary = des1.dtype == np.uint8

    if method == "flann":
        params = (dict(algorithm=1, trees=5), dict(checks=50))
        matcher = cv2.FlannBasedMatcher(*params)
        use_knn, test_ratio = True, 0.75
    elif method == "bf":
        norm = cv2.NORM_HAMMING if is_binary else cv2.NORM_L2
        matcher = cv2.BFMatcher(norm, crossCheck=False)
        use_knn, test_ratio = True, 0.75
    elif method == "flann_lowes":
        params = (dict(algorithm=1, trees=5), dict(checks=50))
        matcher = cv2.FlannBasedMatcher(*params)
        use_knn, test_ratio = True, 0.6
    elif method == "bf_cross":
        norm = cv2.NORM_HAMMING if is_binary else cv2.NORM_L2
        # 交叉验证（双向匹配）
        bf12 = cv2.BFMatcher(norm, crossCheck=False)
        bf21 = cv2.BFMatcher(norm, crossCheck=False)
        try:
            m12 = bf12.knnMatch(des1, des2, k=2)
            m21 = bf21.knnMatch(des2, des1, k=2)
        except cv2.error:
            return []
        # 取双向一致的匹配
        good = []
        for pair in m12:
            if len(pair) == 2 and pair[0].distance < 0.75 * pair[1].distance:
                for pair2 in m21:
                    if len(pair2) == 2 and pair2[0].trainIdx == pair[0].queryIdx:
                        if pair2[0].distance < 0.75 * pair2[1].distance:
                            good.append(pair[0])
                            break
        return good
    elif method == "knn_rank":
        params = (dict(algorithm=1, trees=5), dict(checks=50))
        matcher = cv2.FlannBasedMatcher(*params)
        try:
            matches = matcher.knnMatch(des1, des2, k=2)
        except cv2.error:
            return []
        # 取距离最小的 top-50
        all_m = [m for pair in matches if len(pair) > 0 for m in [pair[0]]]
        all_m.sort(key=lambda x: x.distance)
        return all_m[:min(50, len(all_m))]
    else:
        params = (dict(algorithm=1, trees=5), dict(checks=50))
        matcher = cv2.FlannBasedMatcher(*params)
        use_knn, test_ratio = True, 0.75

    # 通用 knnMatch + Lowe 比率测试
    try:
        knn = matcher.knnMatch(des1, des2, k=2)
    except cv2.error:
        return []

    good = []
    for pair in knn:
        if len(pair) == 2:
            m, n = pair[0], pair[1]
            if m.distance < test_ratio * n.distance:
                good.append(m)
    return good


def localize_image(
    query_image_path: str,
    output_dir: str = "projections/localize",
    feature_method: str = "sift",
    match_method: str = "flann",
    max_iterations: int = 3,
) -> dict:
    """
    端到端视觉定位主函数。

    特征方法: sift, orb, akaze
    匹配方法: flann, bf
    """
    tag = f"{feature_method}_{match_method}"
    log(f"{'='*60}")
    log(f"🚀 视觉定位 [{tag}]: {os.path.basename(query_image_path)}")
    log(f"   特征={feature_method}, 匹配={match_method}, 迭代={max_iterations}")

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # 1. 加载 COLMAP 数据
    known_points, known_images = load_colmap()

    # 2. 读取查询图像
    query_img = cv2.imread(query_image_path)
    if query_img is None:
        return {"success": False, "error": "Cannot read query image", "tag": tag}
    q_h, q_w = query_img.shape[:2]
    camera_matrix = _get_camera_matrix(q_w, q_h)
    log(f"📷 查询图像: {q_w}x{q_h}, 内参矩阵已估算")

    # 3. 提取查询图像特征
    q_kp, q_des = _extract_features(query_img, feature_method)
    if q_des is None or len(q_des) < 10:
        return {"success": False, "error": f"Too few {feature_method} features", "tag": tag}
    log(f"🔍 {feature_method}特征: {len(q_kp)} 个")

    # 4. 与 COLMAP 图像做 3D-2D 匹配
    log("🔗 3D-2D匹配中...")
    pts_3d = []
    pts_2d = []
    used_3d = set()
    best_inlier_count = 0
    best_rvec, best_tvec = None, None
    best_pts_3d, best_pts_2d = None, None
    best_inliers_idx = None

    # 采样 COLMAP 图像
    sample_imgs = known_images[:200]

    for img_idx, img in enumerate(sample_imgs):
        img_path = os.path.join("las", img.name)
        if not os.path.exists(img_path):
            continue

        img_gray = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)
        if img_gray is None:
            continue

        img_kp, img_des = _extract_features(img_gray, feature_method)
        if img_des is None or len(img_des) < 10:
            continue

        # 匹配
        matches = _match_features(q_des, img_des, match_method)
        if len(matches) < 4:
            continue

        # 收集 3D-2D 点对
        local_3d = []
        local_2d = []
        for m in matches:
            q_pt = q_kp[m.queryIdx].pt
            train_idx = m.trainIdx
            if train_idx >= len(img_kp):
                continue

            # 找该 COLMAP 图像在此点的 3D 观测
            for pt2d_x, pt2d_y, pt3d_id in img.points2d:
                if pt3d_id <= 0 or pt3d_id in used_3d:
                    continue
                cd = np.sqrt((img_kp[train_idx].pt[0] - pt2d_x)**2 +
                             (img_kp[train_idx].pt[1] - pt2d_y)**2)
                if cd < 8.0 and pt3d_id in known_points:
                    p3d = known_points[pt3d_id]
                    local_3d.append([p3d.x, p3d.y, p3d.z])
                    local_2d.append([q_pt[0], q_pt[1]])
                    used_3d.add(pt3d_id)
                    break

        if len(local_3d) < 4:
            continue

        # PnP
        rvec_i, tvec_i, inliers_i = _solve_pnp(
            np.array(local_3d, dtype=np.float64),
            np.array(local_2d, dtype=np.float64),
            camera_matrix
        )
        if rvec_i is not None:
            ic = len(inliers_i) if inliers_i is not None else 0
            if ic > best_inlier_count:
                best_inlier_count = ic
                best_rvec, best_tvec = rvec_i, tvec_i
                best_pts_3d, best_pts_2d = local_3d, local_2d
                best_inliers_idx = inliers_i
                log(f"  → COLMAP图像#{img_idx}: {ic}内点 (累积{len(used_3d)}个3D点)")

    if best_rvec is None:
        log("❌ PnP 位姿估计失败")
        return {"success": False, "error": "PnP failed for all images", "tag": tag}

    log(f"✅ PnP成功: 内点={best_inlier_count}")

    # 5. 迭代优化
    rvec, tvec = best_rvec, best_tvec
    inlier_count = best_inlier_count

    for iteration in range(1, max_iterations):
        log(f"🔄 迭代 {iteration+1}/{max_iterations}...")
        reprojected, valid_mask = reproject_points(
            rvec, tvec, camera_matrix,
            _POINT_INDEX["pts"], q_w, q_h
        )
        new_pts_3d, new_pts_2d = [], []
        for i, (px, py) in enumerate(reprojected):
            if not valid_mask[i]:
                continue
            px_i, py_i = int(round(px)), int(round(py))
            for kp in q_kp:
                if abs(kp.pt[0]-px_i) < 10 and abs(kp.pt[1]-py_i) < 10:
                    pid = int(_POINT_INDEX["ids"][i])
                    if pid in known_points:
                        p3d = known_points[pid]
                        new_pts_3d.append([p3d.x, p3d.y, p3d.z])
                        new_pts_2d.append([kp.pt[0], kp.pt[1]])
                    break
        if len(new_pts_3d) >= 4:
            nr, nt, ni = _solve_pnp(np.array(new_pts_3d), np.array(new_pts_2d), camera_matrix)
            if nr is not None:
                nic = len(ni) if ni is not None else 0
                if nic > inlier_count:
                    rvec, tvec = nr, nt
                    inlier_count = nic
                    log(f"  → 优化: {nic}内点")

    # 6. 生成重投影
    log("🖼️ 生成重投影图像...")
    all_pts_3d = np.array([(p.x, p.y, p.z) for p in known_points.values()], dtype=np.float64)
    all_colors = np.array([(p.r, p.g, p.b) for p in known_points.values()], dtype=np.uint8)

    proj_path = str(out / f"reprojection_{tag}.png")
    proj_path, coord_map = render_projection_image(
        all_pts_3d, all_colors, rvec, tvec, camera_matrix, q_w, q_h, proj_path
    )

    coord_path = str(out / f"reprojection_coord_{tag}.json")
    with open(coord_path, "w") as f:
        json.dump({"width": q_w, "height": q_h, "pixels": coord_map}, f)

    # 7. 双图对比 + 连线
    comparison_path = None
    if proj_path:
        proj_img = cv2.imread(proj_path)
        if proj_img is not None:
            h = max(q_h, proj_img.shape[0])
            w = q_w + proj_img.shape[1]
            canvas = np.zeros((h, w, 3), dtype=np.uint8)
            canvas[:q_h, :q_w] = query_img
            canvas[:proj_img.shape[0], q_w:] = proj_img

            colors = [(255,0,0),(0,255,0),(0,0,255),(255,255,0),(255,0,255)]
            if best_inliers_idx is not None and best_pts_2d is not None:
                for i in range(min(5, len(best_inliers_idx))):
                    idx = best_inliers_idx[i][0]
                    if idx < len(best_pts_2d):
                        x1, y1 = int(best_pts_2d[idx][0]), int(best_pts_2d[idx][1])
                        p3d = np.array([best_pts_3d[idx]], dtype=np.float64)
                        pp, _ = cv2.projectPoints(p3d.reshape(1,1,3), rvec, tvec, camera_matrix, None)
                        x2, y2 = int(pp[0,0,0]) + q_w, int(pp[0,0,1])
                        c = colors[i % len(colors)]
                        cv2.circle(canvas, (x1, y1), 6, c, -1)
                        cv2.circle(canvas, (x2, y2), 6, c, -1)
                        cv2.line(canvas, (x1, y1), (x2, y2), c, 2)

            comparison_path = str(out / f"comparison_{tag}.png")
            cv2.imwrite(comparison_path, canvas)
            log(f"✅ 双图对比: {comparison_path}")

    # 8. 位姿
    rmat, _ = cv2.Rodrigues(rvec)
    quat = _rotation_matrix_to_quaternion(rmat)

    result = {
        "success": True,
        "tag": tag,
        "feature_method": feature_method,
        "match_method": match_method,
        "pose": {
            "quaternion": [float(q) for q in quat],
            "translation": tvec.flatten().tolist(),
        },
        "inliers": int(inlier_count),
        "total_3d_points": len(used_3d),
        "reprojection_image": proj_path,
        "comparison_image": comparison_path,
        "coord_map": coord_path,
    }

    log(f"✅ [{tag}] 完成: 内点={inlier_count}")
    log(f"{'='*60}")
    return result
