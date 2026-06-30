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
import torch
import kornia.feature as KF
from kornia.utils import image_to_tensor

from services.las_processor.projection import _load_poses_and_offset

# ── 设备选择 ──
try:
    if torch.backends.mps.is_available():
        DEVICE = torch.device("mps")
        print(f"[LOCALIZER] 使用 MPS (Metal GPU)")
    else:
        DEVICE = torch.device("cpu")
        print(f"[LOCALIZER] 使用 CPU")
except:
    DEVICE = torch.device("cpu")
from PIL import Image, ImageDraw, ImageFont

from services.las_processor.colmap_reader import read_images_txt, read_points3d_txt

# ── 深度学习特征匹配（懒加载） ──────────────
_DL_MATCHER = None  # LightGlueMatcher
_LOFTR = None       # LoFTR

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
    """加载 COLMAP 数据（从 LAS 点云 + images.txt）"""
    global _COLMAP_POINTS, _COLMAP_IMAGES, _POINT_INDEX

    if _COLMAP_POINTS is not None and not force_reload:
        return _COLMAP_POINTS, _COLMAP_IMAGES

    log("加载 COLMAP 数据...")
    t0 = time.time()

    # 加载 images.txt（全景位姿）
    img_path = os.path.join(las_dir, "images.txt")
    _COLMAP_IMAGES = read_images_txt(img_path)
    log(f"  {len(_COLMAP_IMAGES)} 张图像, 耗时{time.time()-t0:.1f}s")

    # 从 LAS 点云构建 3D 点（采样）
    log("从点云构建3D点索引...")
    las_path = os.path.join(las_dir, "default_2026-05-28-112428.las")
    from laspy import open as las_open
    reader = las_open(las_path)
    pts = reader.read()

    # 采样500万点
    step = max(1, len(pts.x) // 5_000_000)
    n_pts = len(pts.x) // step

    # 构建简易 ColmapPoint3D 数据结构
    class _SimplePoint3D:
        def __init__(self, pid, x, y, z, r=128, g=128, b=128):
            self.point_id = pid
            self.x, self.y, self.z = x, y, z
            self.r, self.g, self.b = r, g, b

    _COLMAP_POINTS = {}
    xs = pts.x[::step]
    ys = pts.y[::step]
    zs = pts.z[::step]

    has_rgb = hasattr(pts, 'red') and hasattr(pts, 'green') and hasattr(pts, 'blue')
    rs = pts.red[::step] if has_rgb else None
    gs = pts.green[::step] if has_rgb else None
    bs = pts.blue[::step] if has_rgb else None

    for i in range(len(xs)):
        pid = i + 1
        r = int(rs[i]) if rs is not None else 128
        g = int(gs[i]) if gs is not None else 128
        b = int(bs[i]) if bs is not None else 128
        _COLMAP_POINTS[pid] = _SimplePoint3D(pid, float(xs[i]), float(ys[i]), float(zs[i]), r, g, b)

    log(f"  从点云构建 {len(_COLMAP_POINTS)} 个3D点, 耗时{time.time()-t0:.1f}s")

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
    通过已知图像位姿（images.txt），将查询图像与已知位姿的图像做特征匹配，
    然后利用已知位姿将2D点三角化为3D点。
    
    由于没有 COLMAP 图像文件，改用基于投影图的多 tile 匹配方案。
    """
    return np.array([]), np.array([])


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
    if isinstance(image, str):
        image = cv2.imread(image)
        if image is None:
            return [], None
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
    elif method in ("lightglue", "superpoint", "loftr"):
        # 深度学习方法：返回原始图像用于后续匹配
        kp = [cv2.KeyPoint(0, 0, 1)]  # 占位
        return kp, gray  # 返回灰度图代替descriptor
    else:
        detector = cv2.SIFT_create(nfeatures=3000)
        return detector.detectAndCompute(gray, None)


def _init_dl_matcher():
    """初始化深度学习匹配器（懒加载）"""
    global _DL_MATCHER, _LOFTR
    if _DL_MATCHER is None:
        log(f"加载 LightGlue 模型 ({DEVICE})...")
        _DL_MATCHER = KF.LightGlueMatcher('sift').to(DEVICE)
    if _LOFTR is None:
        log(f"加载 LoFTR 模型 ({DEVICE})...")
        _LOFTR = KF.LoFTR(pretrained='outdoor').to(DEVICE)


def _match_features(des1, des2, method="flann", ratio=0.75, img1=None, img2=None):
    """按指定方法匹配特征

    方法:
      flann        - FLANN kd-tree + Lowe 0.75
      bf           - BruteForce + Lowe 0.75
      flann_lowes  - FLANN + 严格 Lowe 0.6
      bf_cross     - BruteForce + 交叉验证
      knn_rank     - FLANN + top-50
      lightglue    - LightGlue (深度学习)
      loftr        - LoFTR (深度学习)
    """
    # 深度学习方法
    if method in ("lightglue", "loftr"):
        return _match_deep(img1, img2, method)

    # 传统方法
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
        bf12 = cv2.BFMatcher(norm, crossCheck=False)
        bf21 = cv2.BFMatcher(norm, crossCheck=False)
        try:
            m12 = bf12.knnMatch(des1, des2, k=2)
            m21 = bf21.knnMatch(des2, des1, k=2)
        except cv2.error:
            return []
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


def _match_deep(img1_gray, img2_gray, method="lightglue", img1_full=None, img2_full=None):
    """深度学习特征匹配（LightGlue / LoFTR）"""
    _init_dl_matcher()
    
    # 确保输入是灰度图
    if len(img1_gray.shape) == 3:
        img1_gray = cv2.cvtColor(img1_gray, cv2.COLOR_BGR2GRAY)
    if len(img2_gray.shape) == 3:
        img2_gray = cv2.cvtColor(img2_gray, cv2.COLOR_BGR2GRAY)
    
    # 转为 tensor
    h1, w1 = img1_gray.shape
    h2, w2 = img2_gray.shape
    
    if method == "lightglue":
        # LightGlue 需要 SIFT 特征 + 深度学习匹配
        sift = cv2.SIFT_create(nfeatures=2000)
        kp1, des1 = sift.detectAndCompute(img1_gray, None)
        kp2, des2 = sift.detectAndCompute(img2_gray, None)
        if des1 is None or des2 is None or len(des1) < 5 or len(des2) < 5:
            return []
        
        # 转为 tensor (移到 GPU)
        desc1 = torch.from_numpy(des1.astype(np.float32)).unsqueeze(0).to(DEVICE)
        desc2 = torch.from_numpy(des2.astype(np.float32)).unsqueeze(0).to(DEVICE)
        kpts1 = torch.from_numpy(np.array([k.pt for k in kp1], dtype=np.float32)).unsqueeze(0).to(DEVICE)
        kpts2 = torch.from_numpy(np.array([k.pt for k in kp2], dtype=np.float32)).unsqueeze(0).to(DEVICE)
        
        with torch.no_grad():
            result = _DL_MATCHER(desc1, desc2, kpts1, kpts2)
        
        # 解析结果
        matches = []
        if result is not None and len(result) >= 3:
            match_indices = result[0]  # (N, 2) tensor
            for idx in range(match_indices.shape[0]):
                i, j = int(match_indices[idx, 0]), int(match_indices[idx, 1])
                # 创建 cv2.DMatch 兼容对象
                class _DMatch:
                    def __init__(self, qi, ti, d):
                        self.queryIdx = qi
                        self.trainIdx = ti
                        self.distance = d
                matches.append(_DMatch(i, j, 0.0))
        return matches
        
    elif method == "loftr":
        # LoFTR: 端到端匹配（不需要特征点）
        img1_tensor = image_to_tensor(img1_gray, keepdim=True).unsqueeze(0).float().to(DEVICE) / 255.0
        img2_tensor = image_to_tensor(img2_gray, keepdim=True).unsqueeze(0).float().to(DEVICE) / 255.0
        
        with torch.no_grad():
            corr = _LOFTR({
                'image0': img1_tensor,
                'image1': img2_tensor,
            })
        
        class _LoftrMatches(list):
            def __init__(self):
                super().__init__()
                self.kpts0 = None
                self.kpts1 = None
        
        matches = _LoftrMatches()
        if 'keypoints0' in corr and 'keypoints1' in corr:
            kpts0_all = corr['keypoints0']
            kpts1_all = corr['keypoints1']
            if len(kpts0_all) > 0 and len(kpts1_all) > 0:
                kpts0 = kpts0_all[0].cpu().numpy()
                kpts1 = kpts1_all[0].cpu().numpy()
                matches.kpts0 = kpts0
                matches.kpts1 = kpts1
                
                for i in range(min(len(kpts0), len(kpts1))):
                    class _DMatch:
                        def __init__(self, qi, ti, d):
                            self.queryIdx = qi
                            self.trainIdx = ti
                            self.distance = d
                    matches.append(_DMatch(i, i, 0.0))
        
        return matches
    
    return []


def localize_image(
    query_image_path: str,
    output_dir: str = "projections/localize",
    feature_method: str = "sift",
    match_method: str = "flann",
    max_iterations: int = 3,
) -> dict:
    """
    端到端视觉定位主函数。

    使用 panoramicPoses 位姿直接做 PnP 初始化 + 迭代优化：
    1. 加载 panoramicPoses 位姿（局部坐标）
    2. 对每个位姿，将点云投影到图像平面
    3. 在投影位置附近搜索查询图像的特征点
    4. 收集 3D-2D 匹配点对做 PnP
    5. 迭代优化位姿
    """
    tag = f"{feature_method}_{match_method}"
    log(f"{'='*60}")
    log(f"🚀 视觉定位 [{tag}]: {os.path.basename(query_image_path)}")

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # 1. 读取查询图像
    query_img = cv2.imread(query_image_path)
    if query_img is None:
        return {"success": False, "error": "Cannot read query image", "tag": tag}
    q_h_orig, q_w_orig = query_img.shape[:2]
    scale = 512 / max(q_h_orig, q_w_orig)
    q_small = cv2.resize(query_img, (int(q_w_orig * scale), int(q_h_orig * scale))) if scale < 1.0 else query_img
    q_h, q_w = q_small.shape[:2]
    
    camera_matrix = _get_camera_matrix(q_w_orig, q_h_orig, fov_deg=75)
    camera_matrix[0,0] *= scale
    camera_matrix[1,1] *= scale
    camera_matrix[0,2] *= scale
    camera_matrix[1,2] *= scale
    log(f"📷 {q_w_orig}x{q_h_orig} → {q_w}x{q_h}")

    # 2. 提取查询图像 SIFT 特征（用于投影点附近搜索）
    q_gray = cv2.cvtColor(q_small, cv2.COLOR_BGR2GRAY)
    q_kp, q_des = cv2.SIFT_create(nfeatures=3000).detectAndCompute(q_gray, None)
    if q_kp is None:
        return {"success": False, "error": "No features", "tag": tag}
    log(f"🔍 {len(q_kp)} 特征点")

    # 3. 加载点云
    known_points, known_images = load_colmap()
    pts_all = _POINT_INDEX["pts"]  # Nx3, 局部坐标
    log(f"🗺️ {len(pts_all)} 个3D点")

    # 4. 加载 panoramicPoses 位姿
    poses, ox, oy, oz = _load_poses_and_offset()
    log(f"📐 {len(poses)} 个位姿")

    best_inliers = 0
    best_rvec, best_tvec = None, None
    best_3d, best_2d = None, None

    # 5. 对每个位姿做投影匹配
    half_range = 50.0  # 50m 投影范围
    search_radius = 15  # 像素搜索半径

    for pi, pose in enumerate(poses[:min(50, len(poses))]):
        # 筛选位姿附近的点云
        near_mask = (
            (pts_all[:, 0] >= pose['x'] - half_range) & (pts_all[:, 0] <= pose['x'] + half_range) &
            (pts_all[:, 1] >= pose['y'] - half_range) & (pts_all[:, 1] <= pose['y'] + half_range)
        )
        pts_near = pts_all[near_mask]
        if len(pts_near) < 50:
            continue

        # 计算旋转矩阵
        R_cw = _quat_to_rotmat(pose['qx'], pose['qy'], pose['qz'], pose['qw'])
        t_cw = np.array([pose['x'], pose['y'], pose['z']], dtype=np.float64)

        # 世界坐标 → 相机坐标
        pts_cam = (pts_near - t_cw) @ R_cw.T  # Nx3

        # 投影到图像平面
        valid = pts_cam[:, 2] > 0.1  # 相机前方
        if valid.sum() < 30:
            continue
        
        pts_proj = pts_cam[valid]
        px = pts_proj[:, 0] / pts_proj[:, 2] * camera_matrix[0,0] + camera_matrix[0,2]
        py = pts_proj[:, 1] / pts_proj[:, 2] * camera_matrix[1,1] + camera_matrix[1,2]

        # 筛选在图像范围内的点
        in_img = (px >= 0) & (px < q_w) & (py >= 0) & (py < q_h)
        if in_img.sum() < 10:
            continue

        px_in = px[in_img].astype(np.int32)
        py_in = py[in_img].astype(np.int32)
        pts_3d_valid = pts_near[valid][in_img]

        # 在投影点附近搜索查询图像的关键点
        local_3d, local_2d = [], []
        used_pixels = set()
        
        for j in range(len(px_in)):
            key = (px_in[j], py_in[j])
            if key in used_pixels:
                continue
            used_pixels.add(key)

            # 在搜索半径内找关键点
            for kp in q_kp:
                kx, ky = int(kp.pt[0]), int(kp.pt[1])
                if abs(kx - px_in[j]) < search_radius and abs(ky - py_in[j]) < search_radius:
                    local_3d.append(pts_3d_valid[j])
                    local_2d.append([float(kx), float(ky)])
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
            ic = len(inliers_i) if inliers_i is not None else len(local_3d)
            if ic > best_inliers:
                best_inliers = ic
                best_rvec, best_tvec = rvec_i, tvec_i
                best_3d, best_2d = local_3d, local_2d
                log(f"  pose#{pi}: {ic}内点 (最佳)")

    if best_rvec is None:
        return {"success": False, "error": "PnP failed for all poses", "tag": tag}

    log(f"✅ PnP成功: 内点={best_inliers}")

    # 6. 迭代优化
    rvec, tvec = best_rvec, best_tvec
    inlier_count = best_inliers

    for iteration in range(1, max_iterations):
        log(f"🔄 迭代 {iteration+1}/{max_iterations}...")
        reprojected, valid_mask = reproject_points(rvec, tvec, camera_matrix, pts_all, q_w, q_h)
        
        new_3d, new_2d = [], []
        for i, (x_p, y_p) in enumerate(reprojected):
            if not valid_mask[i]:
                continue
            xi, yi = int(round(x_p)), int(round(y_p))
            for kp in q_kp:
                if abs(kp.pt[0]-xi) < search_radius and abs(kp.pt[1]-yi) < search_radius:
                    pid = int(_POINT_INDEX["ids"][i])
                    new_3d.append(pts_all[i].tolist())
                    new_2d.append([kp.pt[0], kp.pt[1]])
                    break

        if len(new_3d) >= 4:
            nr, nt, ni = _solve_pnp(np.array(new_3d), np.array(new_2d), camera_matrix)
            if nr is not None:
                nic = len(ni) if ni is not None else len(new_3d)
                if nic > inlier_count:
                    rvec, tvec = nr, nt
                    inlier_count = nic
                    log(f"  → {nic}内点")

    # 7. 生成重投影
    log("🖼️ 生成重投影图像...")
    all_pts = np.array([(p.x, p.y, p.z) for p in known_points.values()], dtype=np.float64)
    all_col = np.array([(p.r, p.g, p.b) for p in known_points.values()], dtype=np.uint8)

    proj_path = str(out / f"reprojection_{tag}.png")
    proj_path, coord_map = render_projection_image(all_pts, all_col, rvec, tvec, camera_matrix, q_w, q_h, proj_path)

    coord_path = str(out / f"reprojection_coord_{tag}.json")
    with open(coord_path, "w") as f:
        json.dump({"width": q_w, "height": q_h, "pixels": coord_map}, f)

    # 8. 双图对比
    comparison_path = None
    if proj_path:
        proj_img = cv2.imread(proj_path)
        if proj_img is not None and best_3d is not None and len(best_3d) > 0:
            h = max(q_h, proj_img.shape[0])
            w = q_w + proj_img.shape[1]
            canvas = np.zeros((h, w, 3), dtype=np.uint8)
            canvas[:q_h, :q_w] = q_small
            canvas[:proj_img.shape[0], q_w:] = proj_img

            colors = [(255,0,0),(0,255,0),(0,0,255),(255,255,0),(255,0,255)]
            for i in range(min(5, len(best_3d))):
                if i < len(best_2d):
                    x1, y1 = int(best_2d[i][0]), int(best_2d[i][1])
                    p3d = np.array([best_3d[i]], dtype=np.float64)
                    pp, _ = cv2.projectPoints(p3d.reshape(1,1,3), rvec, tvec, camera_matrix, None)
                    x2, y2 = int(pp[0,0,0]) + q_w, int(pp[0,0,1])
                    c = colors[i % len(colors)]
                    cv2.circle(canvas, (x1, y1), 6, c, -1)
                    cv2.circle(canvas, (x2, y2), 6, c, -1)
                    cv2.line(canvas, (x1, y1), (x2, y2), c, 2)

            comparison_path = str(out / f"comparison_{tag}.png")
            cv2.imwrite(comparison_path, canvas)
            log(f"✅ 双图对比: {comparison_path}")

    rmat, _ = cv2.Rodrigues(rvec)
    quat = _rotation_matrix_to_quaternion(rmat)

    result = {
        "success": True,
        "tag": tag,
        "feature_method": "pose_reprojection",
        "match_method": "pnp",
        "pose": {
            "quaternion": [float(q) for q in quat],
            "translation": tvec.flatten().tolist(),
        },
        "inliers": int(inlier_count),
        "total_3d_points": len(best_3d) if best_3d is not None else 0,
        "reprojection_image": proj_path,
        "comparison_image": comparison_path,
        "coord_map": coord_path,
    }

    log(f"✅ [{tag}] 完成: 内点={inlier_count}")
    log(f"{'='*60}")
    return result
