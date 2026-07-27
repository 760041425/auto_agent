"""
SALAD + RomaV2 视觉定位引擎

参考 SLAM-Map 的实现流程：

SALAD 全局检索阶段:
  1. 用 DINOv2 backbone 提取全局描述子（对每张参考投影图离线提取）
  2. 查询图在线提取 → 余弦相似度排序 → top-K 候选
  
LightGlue 稀疏匹配 + 多轮 PnP:
   3. 查询图 vs 候选 tile → LightGlue 稀疏匹配 → 获取对应点
   4. 通过 tile 的像素→3D 坐标映射 → 得到 3D-2D 匹配 → PnP
   5. 多轮迭代：PnP→重投影→LightGlue匹配原图vs重投影→PnP优化

多轮定位:
   6. 每轮用 LightGlue 匹配原图和重投影图，获取更精确的对应关系
  7. PnP → 评估内点 → 如果提升则继续迭代
"""

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import torch
import kornia
from PIL import Image

from services.las_processor.projection import _load_poses_and_offset, _quat_to_rotmat

_logger = logging.getLogger("localizer.salad_roma")
_logger.setLevel(logging.DEBUG)
LOG_DIR = Path(__file__).parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
_fh = logging.FileHandler(str(LOG_DIR / "localizer.log"), mode="a", encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
_logger.handlers.clear()
_logger.addHandler(_fh)
_sh = logging.StreamHandler()
_sh.setFormatter(logging.Formatter("%(asctime)s [SALAD_ROMA] %(message)s", datefmt="%H:%M:%S"))
_logger.addHandler(_sh)


def log(msg: str):
    _logger.info(msg)


# ── 设备 ──
try:
    if torch.backends.mps.is_available():
        DEVICE = torch.device("mps")
        log("使用 MPS (Metal GPU)")
    else:
        DEVICE = torch.device("cpu")
        log("使用 CPU")
except Exception as e:
    DEVICE = torch.device("cpu")
    log(f"设备检测失败: {e}, 使用 CPU")


# ── 全局缓存 ──
_TILE_INDEX: list[dict] | None = None
_SALAD_INDEX: dict[str, np.ndarray] | None = None  # tile_key → global_descriptor
_TILE_IMAGES: dict[str, np.ndarray] | None = None   # tile_key → image (cached)
_ROMA_MODEL = None
_LIGHTGLUE_MODEL = None
_SUPERPOINT_MODEL = None
_DINO_MODEL = None
_DINO_SCALE = None
_PNP_CACHE: dict[str, dict] = {}


def _compute_reprojection_error(rvec, tvec, camera_matrix, object_pts, image_pts) -> float:
    """计算 2D-3D 匹配对应点的平均重投影误差。"""
    if object_pts is None or image_pts is None or len(object_pts) == 0 or len(image_pts) == 0:
        return float("inf")

    projected, _ = cv2.projectPoints(
        np.asarray(object_pts, dtype=np.float64),
        rvec,
        tvec,
        camera_matrix,
        None,
    )
    projected = projected.reshape(-1, 2)
    target = np.asarray(image_pts, dtype=np.float64).reshape(-1, 2)
    if len(projected) != len(target):
        return float("inf")

    diff = projected - target
    return float(np.sqrt(np.sum(diff * diff, axis=1)).mean())


def _is_pose_better(candidate_inliers: int, candidate_error: float, current_inliers: int, current_error: float) -> bool:
    """使用内点数优先、重投影误差次优的策略选择更好的位姿。"""
    if candidate_inliers > current_inliers + 2:
        return True
    if candidate_inliers < current_inliers - 2:
        return False
    return candidate_error < current_error


# ============================================================
#  SALAD: 全局图像检索（DINOv2 全局描述子）
# ============================================================

def _load_tile_index() -> list[dict]:
    global _TILE_INDEX
    if _TILE_INDEX is None:
        idx_path = Path("projections/tile_index.json")
        if idx_path.exists():
            with open(idx_path) as f:
                _TILE_INDEX = json.load(f)
            log(f"  Tile索引: {len(_TILE_INDEX)} 个投影图")
    return _TILE_INDEX or []


def _get_dinov2_model(prefer_small: bool = True):
    """
    SALAD 使用 DINOv2 作为 backbone 提取全局描述子。
    默认使用 DINOv2-S（速度快），可选 DINOv2-L（精度高）。

    从本地缓存加载，绕过 torch.hub.load 的 GitHub 验证。
    将本地 hub 缓存加入 sys.path 后直接 import dinov2.hub.backbones，
    权重由 torch.hub.load_state_dict_from_url 自动使用本地缓存
    (~/.cache/torch/hub/checkpoints/dinov2_vits14_pretrain.pth)。
    """
    global _DINO_MODEL, _DINO_SCALE
    if _DINO_MODEL is not None:
        return _DINO_MODEL, _DINO_SCALE

    hub_cache = os.path.expanduser(
        "~/.cache/torch/hub/facebookresearch_dinov2_main"
    )

    if os.path.exists(hub_cache) and hub_cache not in sys.path:
        sys.path.insert(0, hub_cache)

    models = ['dinov2_vits14', 'dinov2_vitl14'] if prefer_small else ['dinov2_vitl14', 'dinov2_vits14']
    model_descs = {'dinov2_vits14': 'DINOv2-S', 'dinov2_vitl14': 'DINOv2-L'}
    hub_fns = {}

    for model_name in models:
        try:
            if not hub_fns:
                # 只在首次加载时 import
                from dinov2.hub.backbones import dinov2_vits14, dinov2_vitl14
                hub_fns = {'dinov2_vits14': dinov2_vits14, 'dinov2_vitl14': dinov2_vitl14}

            fn = hub_fns[model_name]
            # pretrained=True 会从 dl.fbaipublicfiles.com 下载/使用缓存权重
            model = fn(pretrained=True)
            model = model.to(DEVICE)
            model.eval()
            for p in model.parameters():
                p.requires_grad = False
            log(f"  使用 {model_descs[model_name]} 模型")
            _DINO_MODEL, _DINO_SCALE = model, 1.0
            return _DINO_MODEL, _DINO_SCALE
        except Exception as e:
            log(f"  {model_descs.get(model_name, model_name)} 加载失败: {e}")

    log(f"  DINOv2 所有模型加载失败")
    return None, 0.0


def _extract_dinov2_descriptor(model, img: np.ndarray, scale: float) -> np.ndarray:
    """用 DINOv2 提取单张图像的全局描述子（平均 patch token）"""
    if model is None:
        return None
    
    # 预处理
    h, w = img.shape[:2]
    size = int(max(h, w) * scale)
    size = (size // 14) * 14  # 14 的倍数
    if h > w:
        new_h, new_w = size, int(w * size / h)
    else:
        new_h, new_w = int(h * size / w), size
    new_h = (new_h // 14) * 14
    new_w = (new_w // 14) * 14
    
    img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB) if len(img.shape) == 3 and img.shape[2] == 3 else img
    pil_img = Image.fromarray(img_rgb).resize((new_w, new_h))
    
    # DINOv2 预处理
    from torchvision import transforms
    transform = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])
    tensor = transform(pil_img).unsqueeze(0).to(DEVICE)
    
    with torch.no_grad():
        # DINOv2 forward 返回 dict
        out = model(tensor, is_training=True)
        if isinstance(out, dict):
            # DINOv2-v2: {'x_norm_patchtokens': ..., 'x_norm_clstoken': ...}
            patch_tokens = out.get('x_norm_patchtokens', out.get('x_norm_clstoken'))
        else:
            # Fallback: 直接当 tensor 处理
            patch_tokens = out[:, 1:, :] if out.shape[1] > 1 else out
        # 取所有 patch tokens 的平均作为全局描述子
        desc = patch_tokens.mean(dim=1)  # (1, D)
    
    return desc.cpu().numpy().flatten()


def _extract_multimodal_descriptor(
    model,
    img: np.ndarray,
    normal_map: np.ndarray = None,
    xyz_map: np.ndarray = None,
    scale: float = 1.0
) -> np.ndarray:
    """
    多模态 DINOv2 特征提取：RGB + Normal + XYZ。
    
    各模态分别提取 DINOv2 描述子，然后拼接为联合描述子（3 倍维度）。
    如果某个模态缺失，用零向量填充以保持维度一致。
    """
    rgb_desc = _extract_dinov2_descriptor(model, img, scale)
    if rgb_desc is None:
        return None
    
    base_dim = len(rgb_desc)
    descs = [rgb_desc]
    
    # Normal 模态
    if normal_map is not None and normal_map.size > 0:
        norm_vis = ((normal_map + 1.0) * 127.5).clip(0, 255).astype(np.uint8)
        norm_desc = _extract_dinov2_descriptor(model, norm_vis, scale)
    else:
        norm_desc = None
    descs.append(norm_desc if norm_desc is not None else np.zeros(base_dim, dtype=np.float32))
    
    # XYZ 模态
    if xyz_map is not None and xyz_map.size > 0:
        valid = np.linalg.norm(xyz_map, axis=2) > 1e-6
        if valid.any():
            xyz_vis = xyz_map.copy()
            for c in range(3):
                channel = xyz_vis[..., c]
                c_min, c_max = channel[valid].min(), channel[valid].max()
                if c_max > c_min:
                    channel = (channel - c_min) / (c_max - c_min) * 255.0
                else:
                    channel = np.zeros_like(channel)
                xyz_vis[..., c] = channel
            xyz_vis = np.clip(xyz_vis, 0, 255).astype(np.uint8)
            xyz_desc = _extract_dinov2_descriptor(model, xyz_vis, scale)
        else:
            xyz_desc = None
    else:
        xyz_desc = None
    descs.append(xyz_desc if xyz_desc is not None else np.zeros(base_dim, dtype=np.float32))
    
    return np.concatenate(descs)


def _build_salad_index(force_rebuild: bool = False, progress_callback=None):
    """对全量 tile 提取并缓存 DINOv2 全局描述子"""
    global _SALAD_INDEX
    
    tile_index = _load_tile_index()
    if not tile_index:
        log(f"  ⚠️ tile_index 为空")
        return
    
    cache_path = Path("projections/salad_index.npz")
    if cache_path.exists() and not force_rebuild:
        try:
            data = np.load(cache_path, allow_pickle=True)
            _SALAD_INDEX = dict(zip(data['keys'], data['descs']))
            log(f"  ✅ SALAD 索引已加载: {len(_SALAD_INDEX)} tiles")
            return
        except Exception as e:
            log(f"  ❌ SALAD 索引缓存读取失败: {e}, 将重新构建")
    
    model, scale = _get_dinov2_model(prefer_small=True)
    if model is None:
        log("  ⚠️ DINOv2 不可用，SALAD 检索 fallback 到 SIFT")
        return
    
    valid_tiles = []
    for tile in tile_index:
        if tile.get("view", "") != "top":
            valid_tiles.append(tile)
    
    log(f"  开始提取 {len(valid_tiles)} 张 tile 的 DINOv2 描述子...")
    keys = []
    descs = []
    t0 = time.time()
    
    def process_tile(tile):
        img_path = tile["image_path"]
        img = cv2.imread(img_path)
        if img is None:
            return None
        
        normal_map = None
        normal_path = tile.get("normal_path", "")
        if normal_path and os.path.exists(normal_path):
            normal_map = np.load(normal_path)
        
        xyz_map = None
        npy_path = tile.get("npy_path", "")
        if npy_path and os.path.exists(npy_path):
            xyz_map = np.load(npy_path)
        
        desc = _extract_multimodal_descriptor(model, img, normal_map, xyz_map, scale)
        if desc is not None:
            name_key = os.path.splitext(os.path.basename(img_path))[0]
            return (name_key, desc)
        return None
    
    completed = 0
    total = len(valid_tiles)
    
    for tile in valid_tiles:
        result = process_tile(tile)
        if result is not None:
            keys.append(result[0])
            descs.append(result[1])
        
        completed += 1
        if completed % 50 == 0:
            elapsed = time.time() - t0
            eta = elapsed / completed * (total - completed)
            log(f"    {completed}/{total} tiles, {elapsed:.1f}s, ETA: {eta:.1f}s")
            
            if progress_callback is not None:
                try:
                    progress_callback(completed, total, elapsed)
                except Exception:
                    pass
        
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
    
    if keys:
        _SALAD_INDEX = dict(zip(keys, descs))
        try:
            np.savez_compressed(
                cache_path,
                keys=np.array(keys, dtype=object),
                descs=np.array(descs, dtype=np.float32),
            )
            log(f"  ✅ SALAD 索引构建完成: {len(keys)} tiles, {time.time()-t0:.1f}s, 缓存已保存")
        except Exception as e:
            log(f"  ❌ SALAD 索引缓存保存失败: {e}")
    else:
        log("  ⚠️ SALAD 索引为空")


def _salad_retrieve(q_img: np.ndarray, top_k: int = 5) -> list[tuple[str, float, dict]]:
    """
    SALAD 全局检索：用 DINOv2 描述子余弦相似度搜索最相似的 tile。
    
    返回: [(tile_key, 相似度, tile_info), ...]
    """
    global _SALAD_INDEX
    
    if _SALAD_INDEX is None:
        _build_salad_index()
    
    if _SALAD_INDEX is None or len(_SALAD_INDEX) == 0:
        return []
    
    model, scale = _get_dinov2_model()
    if model is None:
        return []
    
    q_desc = _extract_multimodal_descriptor(model, q_img, None, None, scale)
    if q_desc is None:
        return []
    
    tile_index = _load_tile_index()
    
    valid_keys = []
    valid_descs = []
    valid_tiles = []
    for tile in tile_index:
        view = tile.get("view", "")
        if view == "top":
            continue
        
        name_key = os.path.splitext(os.path.basename(tile["image_path"]))[0]
        ref_desc = _SALAD_INDEX.get(name_key)
        if ref_desc is None:
            continue
        
        valid_keys.append(name_key)
        valid_descs.append(ref_desc)
        valid_tiles.append(tile)
    
    if len(valid_descs) == 0:
        return []
    
    descs_matrix = np.array(valid_descs, dtype=np.float32)
    q_norm = np.linalg.norm(q_desc)
    descs_norms = np.linalg.norm(descs_matrix, axis=1)
    
    similarities = np.dot(descs_matrix, q_desc) / (descs_norms * q_norm + 1e-8)
    
    top_indices = np.argsort(similarities)[::-1][:top_k]
    
    results = []
    for idx in top_indices:
        results.append((valid_keys[idx], float(similarities[idx]), valid_tiles[idx]))
    
    return results


# ============================================================
#  LightGlue: 稀疏匹配
# ============================================================

def _get_roma_model():
    global _ROMA_MODEL
    if _ROMA_MODEL is None:
        t0 = time.time()
        from romatch.models.model_zoo import tiny_roma_v1_model
        from romatch.models.model_zoo import weight_urls

        # 1. 加载 RoMa 权重（从本地缓存）
        url = weight_urls["tiny_roma_v1"]["outdoor"]
        cache_dir = os.path.expanduser("~/.cache/torch/hub/checkpoints")
        os.makedirs(cache_dir, exist_ok=True)
        fname = os.path.basename(url)
        cached_path = os.path.join(cache_dir, fname)

        if os.path.exists(cached_path):
            log(f"  使用本地缓存权重: {cached_path}")
            weights = torch.load(cached_path, map_location=DEVICE, weights_only=True)
        else:
            log(f"  下载权重: {url}")
            weights = torch.hub.load_state_dict_from_url(url, map_location=DEVICE)

        # 2. 加载 XFeat（从本地缓存，绕过 torch.hub.load 的 GitHub 验证）
        xfeat = _load_xfeat_local(device=DEVICE)
        if xfeat is None:
            raise RuntimeError("无法加载 XFeat 模型")

        _ROMA_MODEL = tiny_roma_v1_model(weights=weights, xfeat=xfeat).to(DEVICE)
        log(f"  RoMa 模型加载: {time.time()-t0:.1f}s")
    return _ROMA_MODEL


# XFeat 模型缓存（避免重复加载）
_XFEAT_MODEL = None

def _load_xfeat_local(device):
    """
    从本地缓存加载 XFeat 模型，绕过 torch.hub.load 的 GitHub 验证。

    旧版 torch.hub.load("verlab/accelerated_features", "XFeat") 会访问
    github.com 验证仓库存在，网络受限时返回 403 Forbidden。

    这里直接从本地缓存 ~/.cache/torch/hub/verlab_accelerated_features_main/
    加载 XFeat，权重文件在 {cache_dir}/weights/xfeat.pt。
    """
    global _XFEAT_MODEL
    if _XFEAT_MODEL is not None:
        return _XFEAT_MODEL

    hub_cache = os.path.expanduser(
        "~/.cache/torch/hub/verlab_accelerated_features_main"
    )

    if os.path.exists(hub_cache):
        if hub_cache not in sys.path:
            sys.path.insert(0, hub_cache)

        try:
            from modules.xfeat import XFeat
            # XFeat.__init__ 默认加载本地权重 {cache_dir}/weights/xfeat.pt
            xfeat_full = XFeat(top_k=4096)
            xfeat_full = xfeat_full.to(device).eval()
            # tiny_roma_v1_model 需要 xfeat.net（XFeatModel backbone）
            _XFEAT_MODEL = xfeat_full.net
            log(f"  XFeat 本地加载成功: {hub_cache}/weights/xfeat.pt")
            return _XFEAT_MODEL
        except Exception as e:
            log(f"  XFeat 本地加载失败: {e}")

    # Fallback: torch.hub.load
    log(f"  XFeat 本地缓存未找到，尝试 torch.hub.load")
    try:
        xfeat = torch.hub.load(
            "verlab/accelerated_features", "XFeat", pretrained=True, top_k=4096
        ).net
        _XFEAT_MODEL = xfeat
        return _XFEAT_MODEL
    except Exception as e:
        log(f"  XFeat torch.hub 加载失败: {e}")

    return None


def _get_lightglue_model(device):
    global _LIGHTGLUE_MODEL, _SUPERPOINT_MODEL
    if _LIGHTGLUE_MODEL is not None:
        return _SUPERPOINT_MODEL, _LIGHTGLUE_MODEL
    if _LIGHTGLUE_MODEL is False:
        return None, None

    try:
        import ssl
        try:
            _create_unverified_https_context = ssl._create_unverified_context
        except AttributeError:
            pass
        else:
            ssl._create_default_https_context = _create_unverified_https_context

        # kornia 0.8.3 不支持 SuperPoint 独立提取，使用 DISK
        from kornia.feature import DISK
        disk = DISK().to(device)
        disk.eval()
        _SUPERPOINT_MODEL = disk

        from kornia.feature import LightGlue as _LG
        lightglue = _LG(features='disk').to(device)
        lightglue.eval()
        _LIGHTGLUE_MODEL = lightglue
        log(f"  DISK + LightGlue(disk) 模型加载完成")
        return _SUPERPOINT_MODEL, _LIGHTGLUE_MODEL
    except Exception as e:
        log(f"  DISK+LightGlue 加载失败: {e}, 将使用 SIFT+FLANN fallback")
        _LIGHTGLUE_MODEL = False
        return None, None


def _lightglue_match(img1: np.ndarray, img2: np.ndarray, sample_num: int = 3000) -> tuple:
    """
    LightGlue 稀疏匹配两张图像（使用 SuperPoint 提特征 + LightGlue 匹配）。
    
    返回: (kpts1_np, kpts2_np, certainty_np)
        - kpts1_np: (N, 2) 原图上的关键点坐标（像素）
        - kpts2_np: (N, 2) 目标图上的对应点坐标（像素）
        - certainty_np: (N,) 置信度
    """
    _, model = _get_lightglue_model(DEVICE)
    if model is not None:
        try:
            H1, W1 = img1.shape[:2]
            H2, W2 = img2.shape[:2]

            # 用 DISK 提特征
            fe = _SUPERPOINT_MODEL  # DISK 实例

            img1_t = kornia.image.image_to_tensor(img1, keepdim=False).float() / 255.0
            img2_t = kornia.image.image_to_tensor(img2, keepdim=False).float() / 255.0

            if img1_t.dim() == 3:
                img1_t = img1_t.unsqueeze(0)
                img2_t = img2_t.unsqueeze(0)

            img1_t = img1_t.to(DEVICE)
            img2_t = img2_t.to(DEVICE)

            with torch.no_grad():
                # DISK 返回 list[DISKFeatures]
                feat1_list = fe(img1_t, n=2048)
                feat2_list = fe(img2_t, n=2048)
                feat1 = feat1_list[0]
                feat2 = feat2_list[0]

                # DISKFeatures 有 .keypoints, .descriptors, .scores
                kpts1_3d = feat1.keypoints  # (N, 3) - x, y, score
                kpts2_3d = feat2.keypoints
                desc1 = feat1.descriptors  # (N, 128)
                desc2 = feat2.descriptors

                if len(kpts1_3d) < 2 or len(kpts2_3d) < 2:
                    raise ValueError("DISK 特征点太少")

                # 整理成 LightGlue 需要的格式（keypoints 需归一化到 [-1, 1]）
                kpts0_norm = kpts1_3d[None, :, :2].clone().float()
                kpts1_norm = kpts2_3d[None, :, :2].clone().float()
                kpts0_norm[..., 0] = kpts0_norm[..., 0] / (W1 * 0.5) - 1.0
                kpts0_norm[..., 1] = kpts0_norm[..., 1] / (H1 * 0.5) - 1.0
                kpts1_norm[..., 0] = kpts1_norm[..., 0] / (W2 * 0.5) - 1.0
                kpts1_norm[..., 1] = kpts1_norm[..., 1] / (H2 * 0.5) - 1.0
                kpts0_norm = torch.clamp(kpts0_norm, -0.999, 0.999)
                kpts1_norm = torch.clamp(kpts1_norm, -0.999, 0.999)
                
                data = {
                    "image0": {
                        "keypoints": kpts0_norm,  # [1, N, 2] in [-1, 1]
                        "descriptors": desc1[None],  # [1, N, 128]
                        "image_size": torch.tensor([[W1, H1]], device=DEVICE),
                    },
                    "image1": {
                        "keypoints": kpts1_norm,
                        "descriptors": desc2[None],
                        "image_size": torch.tensor([[W2, H2]], device=DEVICE),
                    },
                }

                out = model(data)
                matches = out.get("matches0", None)
                if matches is not None:
                    match_mask = matches > -1
                    # 还原到像素坐标
                    kpts0_np = kpts1_3d[..., :2][match_mask[0]].cpu().numpy()
                    kpts1_np = kpts2_3d[..., :2][matches[0][match_mask[0]]].cpu().numpy()
                    confidence = out.get("matching_scores0", None)
                    if confidence is not None:
                        cert = confidence[0][match_mask[0]].cpu().numpy().flatten()
                    else:
                        cert = np.ones(len(kpts0_np))
                    if len(kpts0_np) > 0:
                        return kpts0_np, kpts1_np, cert
        except Exception as e:
            log(f"  DISK+LightGlue 匹配失败: {e}, fallback 到 SIFT+FLANN")
            import traceback
            traceback.print_exc()

    # Fallback: SIFT + FLANN（先做 CLAHE 增强暗图对比度）
    def _prepare_gray(img):
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img.copy()
        # 如果图像整体偏暗（均值 < 100），用 CLAHE 提升对比度
        if gray.mean() < 100:
            clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
            gray = clahe.apply(gray)
        return gray

    img1_gray = _prepare_gray(img1)
    img2_gray = _prepare_gray(img2)

    sift = cv2.SIFT_create()
    kp1, desc1 = sift.detectAndCompute(img1_gray, None)
    kp2, desc2 = sift.detectAndCompute(img2_gray, None)

    if kp1 is None or kp2 is None or len(kp1) < 4 or len(kp2) < 4:
        return np.array([]), np.array([]), np.array([])

    FLANN_INDEX_KDTREE = 1
    index_params = dict(algorithm=FLANN_INDEX_KDTREE, trees=5)
    search_params = dict(checks=50)
    flann = cv2.FlannBasedMatcher(index_params, search_params)

    matches = flann.knnMatch(desc1.astype(np.float32), desc2.astype(np.float32), k=2)

    good_matches = []
    for m_n in matches:
        if len(m_n) == 2:
            m, n = m_n[0], m_n[1]
            if m.distance < 0.75 * n.distance:
                good_matches.append(m)

    if len(good_matches) < 4:
        return np.array([]), np.array([]), np.array([])

    kpts1 = np.array([kp1[m.queryIdx].pt for m in good_matches])
    kpts2 = np.array([kp2[m.trainIdx].pt for m in good_matches])
    cert = np.array([1.0 / (1.0 + m.distance) for m in good_matches])

    return kpts1, kpts2, cert


def _roma_match(img1: np.ndarray, img2: np.ndarray, sample_num: int = 3000) -> tuple:
    """向前兼容: 使用 LightGlue 替代 RoMa"""
    return _lightglue_match(img1, img2, sample_num)


def _match_tile_with_lightglue(q_img: np.ndarray, tile_info: dict) -> tuple:
    """用 LightGlue 匹配查询图和 tile 投影图（匹配前先对两张图做 CLAHE 增强）。"""
    tile_img_path = tile_info["image_path"]
    if not os.path.exists(tile_img_path):
        return np.array([]), np.array([]), np.array([])
    
    tile_img = cv2.imread(tile_img_path)
    if tile_img is None:
        return np.array([]), np.array([]), np.array([])
    
    # CLAHE 增强两张图（提升暗区纹理，不改变整体亮度）
    def _clahe(img):
        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        l = clahe.apply(l)
        lab = cv2.merge([l, a, b])
        return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    
    q_clahe = _clahe(q_img)
    tile_clahe = _clahe(tile_img)
    
    log(f"  LightGlue 匹配: 查询图 ({q_img.shape[1]}x{q_img.shape[0]}) vs tile ({tile_img.shape[1]}x{tile_img.shape[0]})")
    t0 = time.time()
    
    kpts_q, kpts_tile, cert = _lightglue_match(q_clahe, tile_clahe)
    
    log(f"  LightGlue 匹配完成: {len(kpts_q)} 点, {time.time()-t0:.1f}s, 平均置信度={cert.mean():.3f}" if len(kpts_q) > 0 else f"  LightGlue 匹配: 0 点")
    
    return kpts_q, kpts_tile, cert


def _match_tile_with_roma(q_img: np.ndarray, tile_info: dict) -> tuple:
    """用 RoMa 密集匹配查询图和 tile 投影图。"""
    tile_img_path = tile_info["image_path"]
    if not os.path.exists(tile_img_path):
        return np.array([]), np.array([]), np.array([])
    tile_img = cv2.imread(tile_img_path)
    if tile_img is None:
        return np.array([]), np.array([]), np.array([])
    log(f"  RoMa 匹配: 查询图 ({q_img.shape[1]}x{q_img.shape[0]}) vs tile ({tile_img.shape[1]}x{tile_img.shape[0]})")
    t0 = time.time()
    kpts_q, kpts_tile, cert = _roma_match(q_img, tile_img)
    log(f"  RoMa 匹配完成: {len(kpts_q)} 点, {time.time()-t0:.1f}s, 平均置信度={cert.mean():.3f}" if len(kpts_q) > 0 else f"  RoMa 匹配: 0 点")
    return kpts_q, kpts_tile, cert


# ============================================================
#  3D-2D 匹配构建 & PnP
# ============================================================

def _build_3d_2d_matches(kpts_q, kpts_tile, cert, coord_map, min_cert=0.001):
    """
    通过 tile 的坐标映射（像素→3D），将 LightGlue 匹配转换为 3D-2D 匹配。
    
    coord_map: NPY 格式 (h, w, 3) float32 数组，无效像素为 [0,0,0]
    """
    if len(kpts_q) == 0 or len(kpts_tile) == 0:
        return np.array([]), np.array([])
    
    object_pts = []  # 3D
    image_pts = []   # 2D
    
    is_numpy = isinstance(coord_map, np.ndarray)
    
    if is_numpy:
        tile_h, tile_w = coord_map.shape[:2]
        for i in range(len(kpts_q)):
            tx, ty = int(round(kpts_tile[i, 0])), int(round(kpts_tile[i, 1]))
            if tx < 0 or tx >= tile_w or ty < 0 or ty >= tile_h:
                continue
            
            p3d = coord_map[ty, tx]
            if np.allclose(p3d, [0, 0, 0], atol=1e-6):
                continue
            
            if cert is not None and i < len(cert) and cert[i] < min_cert:
                continue
            
            object_pts.append([float(p3d[0]), float(p3d[1]), float(p3d[2])])
            image_pts.append([float(kpts_q[i, 0]), float(kpts_q[i, 1])])
    else:
        tile_h = max(int(k.split(',')[1]) for k in coord_map.keys()) + 1 if coord_map else 512
        for i in range(len(kpts_q)):
            tx, ty = int(round(kpts_tile[i, 0])), int(round(kpts_tile[i, 1]))
            key_forward = f"{tx},{ty}"
            key_flipped = f"{tx},{tile_h - 1 - ty}"
            
            p3d = coord_map.get(key_forward) or coord_map.get(key_flipped)
            if p3d is None or len(p3d) < 3:
                continue
            
            if cert is not None and i < len(cert) and cert[i] < min_cert:
                continue
            
            object_pts.append([float(p3d[0]), float(p3d[1]), float(p3d[2])])
            image_pts.append([float(kpts_q[i, 0]), float(kpts_q[i, 1])])
    
    return np.array(object_pts, dtype=np.float64), np.array(image_pts, dtype=np.float64)


def _solve_pnp(object_pts, image_pts, camera_matrix):
    """PnP 位姿估计"""
    if len(object_pts) < 4:
        log(f"    [PnP] 点太少: {len(object_pts)} < 4")
        return None, None, None
    
    log(f"    [PnP] 输入: {len(object_pts)} 对 3D-2D 匹配")
    if len(object_pts) > 0:
        log(f"    [PnP] 3D点范围: X={object_pts[:,0].min():.1f}~{object_pts[:,0].max():.1f}, Y={object_pts[:,1].min():.1f}~{object_pts[:,1].max():.1f}, Z={object_pts[:,2].min():.1f}~{object_pts[:,2].max():.1f}")
        log(f"    [PnP] 2D点范围: X={image_pts[:,0].min():.1f}~{image_pts[:,0].max():.1f}, Y={image_pts[:,1].min():.1f}~{image_pts[:,1].max():.1f}")
    
    dist_coeffs = np.zeros((4, 1))
    success, rvec, tvec, inliers = cv2.solvePnPRansac(
        object_pts, image_pts, camera_matrix, dist_coeffs,
        iterationsCount=2000, reprojectionError=8.0, confidence=0.85,
        flags=cv2.SOLVEPNP_ITERATIVE,
    )
    
    if not success:
        log(f"    [PnP] 求解失败")
        return None, None, None
    
    ic = len(inliers) if inliers is not None else len(object_pts)
    log(f"    [PnP] 成功: {ic}/{len(object_pts)} 内点, tvec={tvec.flatten()[:3]}")
    return rvec, tvec, inliers


def _rotation_matrix_to_quaternion(R):
    q = np.zeros(4)
    q[0] = np.sqrt(1 + R[0,0] + R[1,1] + R[2,2]) / 2
    q[1] = (R[2,1] - R[1,2]) / (4 * q[0])
    q[2] = (R[0,2] - R[2,0]) / (4 * q[0])
    q[3] = (R[1,0] - R[0,1]) / (4 * q[0])
    return q


def _get_camera_matrix(img_w, img_h, fov_deg=75):
    f = max(img_w, img_h) / (2 * np.tan(np.deg2rad(fov_deg / 2)))
    return np.array([
        [f, 0, img_w / 2],
        [0, f, img_h / 2],
        [0, 0, 1]
    ], dtype=np.float64)


def _compute_reprojection_coords(points_3d, point_colors, rvec, tvec, camera_matrix, img_w, img_h):
    """仅计算重投影的像素→3D 映射（不写文件），用于 LightGlue 迭代时的 3D-2D 匹配"""
    w, h = int(img_w), int(img_h)
    projected, valid = _reproject_point_cloud(rvec, tvec, camera_matrix, points_3d, w, h)
    if len(projected) == 0:
        return {}
    coord_map = {}
    valid_3d = points_3d[valid]
    for i in range(len(projected)):
        px, py = int(round(projected[i, 0])), int(round(projected[i, 1]))
        if px < 0 or px >= w or py < 0 or py >= h:
            continue
        key = f"{px},{py}"
        if key not in coord_map:
            coord_map[key] = [float(v) for v in valid_3d[i]]
    return coord_map


def _reproject_point_cloud(rvec, tvec, camera_matrix, pts_3d, img_w, img_h):
    """投影3D点云到图像平面"""
    if len(pts_3d) == 0:
        return np.array([]), np.array([])
    
    projected, _ = cv2.projectPoints(pts_3d, rvec, tvec, camera_matrix, None)
    projected = projected.reshape(-1, 2)
    
    valid = (projected[:, 0] >= 0) & (projected[:, 0] < img_w) & \
            (projected[:, 1] >= 0) & (projected[:, 1] < img_h)
    
    return projected[valid], valid


def _rvec_tvec_to_colmap_line(rvec, tvec):
    """将 PnP 的 rvec/tvec 转为 octree_render 需要的 colmap 行格式。

    rvec: Rodrigues 旋转向量 → 旋转矩阵 → 四元数
    tvec: 平移向量（局部坐标）

    COLMAP 约定: X_cam = R_wc * (X_world - t_wc)
    所以 tvec = t_wc (世界坐标下的相机位置经旋转后的值)
    四元数表示 R_wc (world→camera 旋转)
    """
    R, _ = cv2.Rodrigues(rvec)
    q = _rotation_matrix_to_quaternion(R)
    qw, qx, qy, qz = float(q[0]), float(q[1]), float(q[2]), float(q[3])
    tx, ty, tz = float(tvec.flatten()[0]), float(tvec.flatten()[1]), float(tvec.flatten()[2])
    return f"{qw:.10f} {qx:.10f} {qy:.10f} {qz:.10f} {tx:.6f} {ty:.6f} {tz:.6f}"


def render_projection_image(
    points_3d, point_colors, rvec, tvec, camera_matrix,
    img_w, img_h, output_path, include_coord_map=False,
):
    """渲染重投影图像 — 优先用 octree_render (C++引擎)，fallback 到 Python 渲染。"""
    w, h = int(img_w), int(img_h)
    tvec_local = np.array(tvec).flatten()[:3]

    # ── 尝试 octree_render（高质量）──
    try:
        from services.las_processor.projection_octree import (
            render_pose_octree, _depth_to_xyz_map, OCTREE_CONFIG
        )
        from services.las_processor.projection import _apply_camera_like_shading
        from PIL import Image
        import tempfile

        octree_dataset = 'projections/octree_data'
        if os.path.exists(os.path.join(octree_dataset, 'manifest.json')):
            colmap_line = _rvec_tvec_to_colmap_line(rvec, tvec_local)
            log(f"  [RENDER] octree_render colmap: {colmap_line}")

            # 计算 focal_norm（与批量投影一致）
            fov_deg = 75
            f = max(w, h) / (2 * np.tan(np.deg2rad(fov_deg / 2)))
            focal_norm = f / max(w, h)
            fx = fy = f
            cx = (w - 1) / 2.0
            cy = (h - 1) / 2.0

            with tempfile.TemporaryDirectory(prefix='pnp_render_') as tmpdir:
                color_ppm = os.path.join(tmpdir, 'color.ppm')
                depth_raw = os.path.join(tmpdir, 'depth.raw')

                ok = render_pose_octree(
                    octree_dataset, colmap_line,
                    w, h, focal_norm,
                    color_ppm, depth_raw,
                )

                if ok and os.path.exists(color_ppm):
                    # 后处理（与批量投影一致）
                    with Image.open(color_ppm) as img:
                        color_img = np.array(img.convert('RGB'))

                    coord_map = {}
                    if os.path.exists(depth_raw):
                        depth = np.fromfile(depth_raw, dtype=np.float32)
                        if depth.size == w * h:
                            depth = depth.reshape(h, w)
                            color_img = _apply_camera_like_shading(color_img, depth=depth)

                            if include_coord_map:
                                rl_parts = colmap_line.split()
                                rl_q = [float(x) for x in rl_parts[:4]]
                                rl_t = [float(x) for x in rl_parts[4:7]]
                                _, world_array = _depth_to_xyz_map(
                                    depth, fx, fy, cx, cy,
                                    *rl_q, *rl_t, (0, 0, 0)
                                )
                                valid = np.any(world_array != 0, axis=2)
                                for py_i in range(h):
                                    for px_i in range(w):
                                        if valid[py_i, px_i]:
                                            coord_map[f"{px_i},{py_i}"] = [
                                                float(world_array[py_i, px_i, 0]),
                                                float(world_array[py_i, px_i, 1]),
                                                float(world_array[py_i, px_i, 2]),
                                            ]
                    else:
                        color_img = _apply_camera_like_shading(color_img)

                    # 保存为 PNG
                    cv2.imwrite(output_path, cv2.cvtColor(color_img, cv2.COLOR_RGB2BGR))
                    log(f"  [RENDER] octree_render 成功: {output_path}")
                    return output_path, coord_map
                else:
                    log(f"  [RENDER] octree_render 无输出，fallback 到 Python 渲染")
    except Exception as e:
        log(f"  [RENDER] octree_render 异常: {e}，fallback 到 Python 渲染")

    # ── Fallback: Python 渲染 ──
    log(f"  [RENDER] 使用 Python 渲染 (fallback)")
    projected, valid = _reproject_point_cloud(rvec, tvec_local, camera_matrix, points_3d, w, h)
    if len(projected) == 0:
        return None, {}

    from services.localizer import _render_point_cloud_splat
    img = _render_point_cloud_splat(points_3d, point_colors, camera_matrix, w, h, rvec, tvec_local, radius=1.2)
    coord_map = {}
    if include_coord_map:
        valid_3d = points_3d[valid]
        px = np.rint(projected[:, 0]).astype(np.int32)
        py = np.rint(projected[:, 1]).astype(np.int32)
        pixel_ids = py * w + px
        _, unique_indices = np.unique(pixel_ids, return_index=True)
        coord_map = {
            f"{px[i]},{py[i]}": [float(v) for v in valid_3d[i]]
            for i in unique_indices
        }

    try:
        cv2.imwrite(output_path, img)
    except Exception as e:
        log(f"  ⚠️ 图像写入警告 ({output_path}): {e}")
    return output_path, coord_map


# ============================================================
#  主定位流程：SALAD + LightGlue + 多轮PnP
# ============================================================

def localize_with_salad_roma(
    query_image_path: str,
    output_dir: str = "projections/localize",
    max_iterations: int = 3,
    top_k_retrieval: int = 3,
    debug_visualizations: bool = False,
    algo: str = "lightglue",
) -> dict:
    """
    SALAD 全局检索 → 匹配 → PnP 视觉定位。
    
    支持算法:
      - "roma": SALAD + RoMa 密集匹配
      - "lightglue": SALAD + LightGlue 稀疏匹配
      - "ace": ACE 场景坐标回归 + PnP
    """
    from services.localizer import load_colmap, _POINT_INDEX, get_point_cloud_arrays
    
    match_name = {"roma": "RoMa", "lightglue": "LightGlue", "ace": "ACE"}.get(algo, "LightGlue")
    tag = f"salad_{algo}"
    log(f"{'='*60}")
    log(f"🚀 SALAD+{match_name} 定位: {os.path.basename(query_image_path)}")
    
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    
    # ── PnP 缓存检查 ──
    cache_key = os.path.abspath(query_image_path)
    if cache_key in _PNP_CACHE:
        # 删除缓存，强制重新定位（用户修改定位参数后需要重新跑）
        del _PNP_CACHE[cache_key]
        log(f"🔄 清除旧缓存，强制重新定位")
    
    # 1. 读取查询图像
    query_img = cv2.imread(query_image_path)
    if query_img is None:
        return {"success": False, "error": "Cannot read query image", "tag": tag}
    
    # ACE 模式：直接预测 3D 坐标 → PnP，无需匹配
    if algo == "ace":
        log(f"  🧠 ACE 场景坐标回归模式")
        from services.localizer.ace_trainer import ACERegressor, ace_localize as ace_loc
        
        model_path = Path("projections/ace_model.pth")
        if not model_path.exists():
            return {"success": False, "error": "ACE 模型未训练", "tag": tag}
        
        try:
            # 检测模型架构
            sd = torch.load(model_path, map_location=DEVICE, weights_only=True)
            has_decoder = any('dec' in k for k in sd)
            has_encoder = any('encoder' in k for k in sd)
            
            if has_encoder:
                from services.localizer.ace_trainer import ACERegressor
                model = ACERegressor(mean=torch.zeros(3), num_head_blocks=1, use_homogeneous=False)
                log(f"  ACE 使用 ACERegressor 架构")
            else:
                from services.localizer.ace_trainer import CoordRegressionFCN
                model = CoordRegressionFCN(in_channels=6)
                log(f"  ACE 使用 CoordRegressionFCN 架构")
            
            model.load_state_dict(sd, strict=False)
            model.eval().to(DEVICE)
            log(f"  ACE 模型加载完成, device={next(model.parameters()).device}")
            
            h_orig, w_orig = query_img.shape[:2]
            fov_deg = 75
            f = max(w_orig, h_orig) / (2 * np.tan(np.deg2rad(fov_deg / 2)))
            K = np.array([[f, 0, w_orig/2], [0, f, h_orig/2], [0, 0, 1]])
            
            success_ace, rvec, tvec, inliers = ace_loc(model, query_img, K, None)
            if success_ace:
                from scipy.spatial.transform import Rotation as R
                q_ace = R.from_matrix(cv2.Rodrigues(rvec)[0]).as_quat()
                t = tvec.flatten()
                
                # 用 ACE 位姿渲染投影图做对比
                ace_proj = out / f"{tag}_ace_proj.png"
                ace_comp = out / f"{tag}_ace_comparison.jpg"
                try:
                    from services.las_processor.projection_octree import (
                        OCTREE_RENDER_BIN, OCTREE_CONFIG, OCTREE_SOURCE_DIR,
                        _build_colmap_line
                    )
                    import subprocess as _sp
                    # 构建 colmap 线（ACE 预测的位姿转成 COLMAP 格式）
                    ace_pose = {
                        'x': float(t[0]), 'y': float(t[1]), 'z': float(t[2]),
                        'qw': float(q_ace[3]), 'qx': float(q_ace[0]),
                        'qy': float(q_ace[1]), 'qz': float(q_ace[2]),
                    }
                    colmap_line = _build_colmap_line(ace_pose, (0,0,0), 0)
                    # octree_render
                    octree_dataset = "projections/octree_data"
                    if Path(octree_dataset).exists():
                        _sp.run([
                            OCTREE_RENDER_BIN, '--dataset', octree_dataset,
                            '--colmap', colmap_line,
                            '--image-width', '512', '--image-height', '512',
                            '--focal-normalized', '0.75',
                            '--color-output', str(ace_proj),
                            '--config', OCTREE_CONFIG,
                        ], capture_output=True, text=True, timeout=120)
                except Exception as render_err:
                    log(f"  ACE 重投影失败: {render_err}")
                
                # 对比图：左=查询图，右=重投影
                if ace_proj.exists():
                    tile = cv2.imread(str(ace_proj))
                    if tile is not None:
                        q_s = cv2.resize(query_img, (512, 512))
                        comp = np.hstack([q_s, tile])
                        # 加标签
                        cv2.putText(comp, "query", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,255,0), 2)
                        cv2.putText(comp, f"ACE reproj ({t[0]:.1f},{t[1]:.1f},{t[2]:.1f})", (522, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,255,0), 2)
                        cv2.imwrite(str(ace_comp), comp)
                
                return {
                    "success": True,
                    "tag": tag,
                    "pose": {
                        "quaternion": [float(q_ace[3]), float(q_ace[0]), float(q_ace[1]), float(q_ace[2])],
                        "translation": [float(t[0]), float(t[1]), float(t[2])],
                    },
                    "inliers": len(inliers) if inliers is not None else 0,
                    "comparison_image": str(ace_comp) if ace_comp.exists() else "",
                }
            return {"success": False, "error": "ACE 定位失败", "tag": tag}
        except Exception as e:
            return {"success": False, "error": f"ACE 错误: {e}", "tag": tag}
    
    q_h_orig, q_w_orig = query_img.shape[:2]
    # 统一缩放到 512x512 正方形
    q_small = cv2.resize(query_img, (512, 512))
    q_h, q_w = 512, 512
    scale_x = 512 / q_w_orig
    scale_y = 512 / q_h_orig
    camera_matrix = _get_camera_matrix(q_w_orig, q_h_orig, fov_deg=75)
    camera_matrix[0,0] *= scale_x
    camera_matrix[1,1] *= scale_y
    camera_matrix[0,2] = 256.0
    camera_matrix[1,2] = 256.0
    log(f"📷 {q_w_orig}x{q_h_orig} → {q_w}x{q_h} (512x512)")
    
    # 2. 加载点云
    known_points, _ = load_colmap()
    pts_all = _POINT_INDEX["pts"]
    all_pts, all_col = get_point_cloud_arrays()
    log(f"🗺️ {len(pts_all)} 个3D点")
    
    # 3. SALAD 全局检索
    log("🔍 SALAD 全局检索...")
    t0 = time.time()
    retrieved = _salad_retrieve(q_small, top_k=top_k_retrieval)
    log(f"  SALAD 检索: {time.time()-t0:.1f}s, 找到 {len(retrieved)} 候选")
    
    if not retrieved:
        log("  SALAD 检索无结果")
        return {"success": False, "error": "SALAD 检索无匹配候选", "tag": tag}
    
    for i, (name_key, sim, tile) in enumerate(retrieved):
        log(f"  #{i}: {tile.get('view','?')}/{name_key} 相似度={sim:.4f}")
    
    # 4. 对 top-1 候选做 LightGlue 匹配 + PnP
    best_rvec, best_tvec = None, None
    best_inliers = 0
    best_reproj_error = float("inf")
    best_3d, best_2d = None, None
    best_pose = None
    
    for rank, (name_key, sim, tile) in enumerate(retrieved):
        log(f"\n{'─'*40}")
        log(f"  LightGlue 匹配候选 #{rank}: {tile['view']}/{name_key} (sim={sim:.3f})")
        
        # 跳过被过滤的 tile（image_path 为空）
        if not tile.get("image_path") or not os.path.exists(tile.get("image_path", "")):
            log(f"    跳过: tile 文件不存在")
            continue
        
        # 加载 coord_map（NPY 格式）
        npy_path = tile.get("npy_path", "")
        if not npy_path or not os.path.exists(npy_path) or os.path.isdir(npy_path):
            npy_path = f"projections/tiles/view_{tile['view']}_{tile['tile']}_{name_key.split('_')[-1]}.npy"
        if not os.path.exists(npy_path) or os.path.isdir(npy_path):
            alt = Path("projections/tiles") / Path(npy_path).name
            npy_path = str(alt)
        if not os.path.exists(npy_path) or os.path.isdir(npy_path):
            log(f"    坐标映射不存在: {npy_path}")
            continue
        
        coord_map = np.load(npy_path)
        
        # 根据算法选择匹配器
        _matcher = {"roma": _match_tile_with_roma, "lightglue": _match_tile_with_lightglue}.get(algo, _match_tile_with_lightglue)
        kpts_q, kpts_tile, cert = _matcher(q_small, tile)
        if len(kpts_q) < 10:
            log(f"    匹配点太少, 跳过")
            continue
        
        # 构建 3D-2D 匹配（LightGlue 置信度偏低，用较低阈值）
        obj_pts, img_pts = _build_3d_2d_matches(kpts_q, kpts_tile, cert, coord_map, min_cert=0.001)
        log(f"    3D-2D 匹配: {len(obj_pts)} 对")
        
        if len(obj_pts) < 4:
            continue
        
        # PnP
        rvec_i, tvec_i, inliers_i = _solve_pnp(obj_pts, img_pts, camera_matrix)
        if rvec_i is not None:
            ic = len(inliers_i) if inliers_i is not None else len(obj_pts)
            log(f"    PnP: {ic}/{len(obj_pts)} 内点")
            reproj_error_i = _compute_reprojection_error(rvec_i, tvec_i, camera_matrix, obj_pts, img_pts)
            if _is_pose_better(ic, reproj_error_i, best_inliers, best_reproj_error):
                best_inliers = ic
                best_reproj_error = reproj_error_i
                best_rvec, best_tvec = rvec_i, tvec_i
                best_3d, best_2d = (
                    obj_pts[inliers_i.flatten()] if inliers_i is not None else obj_pts,
                    img_pts[inliers_i.flatten()] if inliers_i is not None else img_pts,
                )
                best_pose = tile
    
    # 5. 多轮迭代：SALAD 相似度评估 → LightGlue 匹配 → PnP → 重投影 → SALAD 再评估
    #    每轮用 SALAD 对比重投影图和原图的相似度，选相似度最高的位姿
    if best_rvec is not None:
        log(f"\n{'─'*40}")
        log(f"🔄 多轮 PnP 迭代优化 (SALAD 相似度引导)...")
        
        # 获取 DINOv2 模型用于 SALAD 相似度评估（仅记录，不再用它做主判断）
        salad_model, salad_scale = _get_dinov2_model()
        
        # 存储所有候选结果，便于回溯和日志展示
        candidates = []
        
        # 初始结果：tile 检索后的首轮 PnP
        init_proj = str(out / f"_init_{tag}.png")
        init_proj_path, init_coord = render_projection_image(
            all_pts, all_col, best_rvec, best_tvec, camera_matrix, q_w, q_h, init_proj
        )
        init_sim = 0.0
        if init_proj_path and salad_model is not None:
            init_proj_img = cv2.imread(init_proj_path)
            if init_proj_img is not None:
                init_desc = _extract_multimodal_descriptor(salad_model, init_proj_img, None, None, salad_scale)
                q_desc = _extract_multimodal_descriptor(salad_model, q_small, None, None, salad_scale)
                if init_desc is not None and q_desc is not None:
                    init_sim = float(np.dot(init_desc, q_desc) / (np.linalg.norm(init_desc) * np.linalg.norm(q_desc) + 1e-8))
                    log(f"  初始 SALAD 相似度: {init_sim:.4f}")
        init_error = _compute_reprojection_error(best_rvec, best_tvec, camera_matrix, best_3d, best_2d)
        candidates.append((init_sim, best_rvec, best_tvec, best_inliers, init_error, best_3d, best_2d))
        # 清理
        for tmp_path in [init_proj]:
            if os.path.exists(tmp_path): os.remove(tmp_path)
        
        # ── 10 轮迭代优化：相似度不再提升时停止，取最佳 ──
        MAX_ROUNDS = 10

        rvec, tvec = best_rvec, best_tvec
        inlier_count = best_inliers
        current_error = init_error

        # 用当前最佳位姿先评估一轮 SALAD 相似度
        check_proj = str(out / f"_check_{tag}.png")
        check_proj_path, _ = render_projection_image(
            all_pts, all_col, best_rvec, best_tvec, camera_matrix, q_w, q_h, check_proj
        )
        current_salad_sim = 0.0
        if check_proj_path and salad_model is not None:
            check_img = cv2.imread(check_proj_path)
            if check_img is not None:
                check_desc = _extract_multimodal_descriptor(salad_model, check_img, None, None, salad_scale)
                q_desc = _extract_multimodal_descriptor(salad_model, q_small, None, None, salad_scale)
                if check_desc is not None and q_desc is not None:
                    current_salad_sim = float(np.dot(check_desc, q_desc) / (np.linalg.norm(check_desc) * np.linalg.norm(q_desc) + 1e-8))
                    log(f"  初始 SALAD 相似度: {current_salad_sim:.4f}")
        if os.path.exists(check_proj): os.remove(check_proj)

        prev_best_sim = current_salad_sim  # 记录上一轮最佳相似度
        no_improve_count = 0  # 连续无提升轮数

        for iteration in range(1, MAX_ROUNDS + 1):
            # 连续2轮相似度未提升 → 停止（说明已收敛或发散）
            if no_improve_count >= 2:
                log(f"  ⏹ 相似度连续{no_improve_count}轮未提升 (当前={current_salad_sim:.4f}, 历史最佳={prev_best_sim:.4f}), 停止 (第{iteration}轮)")
                break

            log(f"\n  {'─'*30}")
            log(f"  迭代 {iteration}/{MAX_ROUNDS} (当前相似度={current_salad_sim:.4f})...")
            log(f"    当前状态: 内点={inlier_count}, 重投影误差={current_error:.3f}")
            log(f"    当前位姿: tvec={tvec.flatten()[:3] if hasattr(tvec, 'flatten') else tvec[:3]}")

            # 用当前位姿渲染重投影图
            iter_proj = str(out / f"_iter_{tag}_{iteration}.png")
            iter_proj_path, _ = render_projection_image(
                all_pts, all_col, rvec, tvec, camera_matrix, q_w, q_h, iter_proj
            )
            if iter_proj_path is None:
                log(f"    重投影失败, 停止迭代")
                break

            # LightGlue 匹配原图 vs 重投影图
            iter_img = cv2.imread(iter_proj_path)
            if iter_img is None:
                log(f"    无法读取重投影图, 停止迭代")
                break

            kpts_q2, kpts_proj, cert2 = _lightglue_match(q_small, iter_img, sample_num=2000)
            if len(kpts_q2) < 10:
                log(f"    迭代匹配点太少, 停止")
                break

            # 通过重投影获取 coord_map（像素→3D 映射）
            iter_coord = _compute_reprojection_coords(
                all_pts, all_col, rvec, tvec, camera_matrix, q_w, q_h
            )

            obj_pts2, img_pts2 = _build_3d_2d_matches(kpts_q2, kpts_proj, cert2, iter_coord)
            log(f"    3D-2D: {len(obj_pts2)} 对")

            if len(obj_pts2) < 4:
                log(f"    3D-2D 匹配不足, 停止迭代")
                break

            nr, nt, ni = _solve_pnp(obj_pts2, img_pts2, camera_matrix)
            if nr is not None:
                nic = len(ni) if ni is not None else len(obj_pts2)
                log(f"    PnP: {nic}/{len(obj_pts2)} 内点")

                # 用新位姿渲染并评估 SALAD 相似度
                new_proj = str(out / f"_new_{tag}_{iteration}.png")
                new_proj_path, _ = render_projection_image(
                    all_pts, all_col, nr, nt, camera_matrix, q_w, q_h, new_proj
                )
                new_sim = 0.0
                if new_proj_path and salad_model is not None:
                    new_img = cv2.imread(new_proj_path)
                    if new_img is not None:
                        new_desc = _extract_multimodal_descriptor(salad_model, new_img, None, None, salad_scale)
                        q_desc = _extract_multimodal_descriptor(salad_model, q_small, None, None, salad_scale)
                        if new_desc is not None and q_desc is not None:
                            new_sim = float(np.dot(new_desc, q_desc) / (np.linalg.norm(new_desc) * np.linalg.norm(q_desc) + 1e-8))
                            log(f"    新位姿 SALAD 相似度: {new_sim:.4f}")

                candidate_error = _compute_reprojection_error(nr, nt, camera_matrix, obj_pts2, img_pts2)
                candidates.append((new_sim, nr, nt, nic, candidate_error, obj_pts2, img_pts2))

                # 更新当前相似度用于下一轮判断
                current_salad_sim = new_sim

                # 判断相似度是否提升（允许 0.01 容差，避免微小波动）
                if new_sim > prev_best_sim + 0.01:
                    prev_best_sim = new_sim
                    no_improve_count = 0
                    log(f"    ✅ 相似度提升: {new_sim:.4f} (最佳)")
                else:
                    no_improve_count += 1
                    log(f"    相似度未提升: {new_sim:.4f} (连续{no_improve_count}轮)")

                if _is_pose_better(nic, candidate_error, inlier_count, current_error):
                    rvec, tvec = nr, nt
                    inlier_count = nic
                    best_3d, best_2d = obj_pts2, img_pts2
                    current_error = candidate_error
                    log(f"    ✅ 几何解提升: 内点={nic}, 重投影误差={candidate_error:.3f}")
                else:
                    log(f"    几何解未提升: 内点={nic}, 重投影误差={candidate_error:.3f} (当前={inlier_count}, {current_error:.3f})")

            # 清理中间文件
            tmp_files = [iter_proj]
            if 'new_proj' in dir():
                tmp_files.append(new_proj)
            for tmp in tmp_files:
                if os.path.exists(tmp):
                    os.remove(tmp)

        # 从所有候选中选择最佳结果：内点数优先，重投影误差次优
        candidates.sort(key=lambda x: (-x[3], x[4]))
        best_sim, best_rvec, best_tvec, best_inliers, best_reproj_error, best_3d, best_2d = candidates[0]
        
        # 构造迭代历史（供前端展示）
        iter_history = []
        for i, (sim, rv, tv, ic, err, b3d, b2d) in enumerate(candidates):
            iter_history.append({
                "round": i,
                "salad_similarity": round(sim, 4),
                "pnp_inliers": ic,
                "reprojection_error": round(err, 3),
            })
        
        log(f"\n{'─'*40}")
        log(f"🏆 最佳结果: SALAD 相似度={best_sim:.4f}, PnP内点={best_inliers}, 重投影误差={best_reproj_error:.3f}")
        
        # 缓存结果（含迭代历史和所有中间位姿供后续优化）
        _PNP_CACHE[cache_key] = {
            'rvec': best_rvec, 'tvec': best_tvec, 'inliers': best_inliers,
            'best_3d': best_3d, 'best_2d': best_2d,
            'q_small': q_small, 'q_w': q_w, 'q_h': q_h,
            'camera_matrix': camera_matrix, 'known_points': known_points,
            'q_kp': None, 'q_des': None,
            'iter_history': iter_history,
            'total_rounds': len(candidates),
            'all_candidates': [
                {'round': i, 'rvec': rv.tolist() if hasattr(rv, 'tolist') else rv,
                 'tvec': tv.tolist() if hasattr(tv, 'tolist') else tv,
                 'salad_similarity': round(s, 4), 'pnp_inliers': ic}
                for i, (s, rv, tv, ic, err, b3d, b2d) in enumerate(candidates)
            ],
            'all_pts': all_pts, 'all_col': all_col,
        }
        
        # 候选图只在显式调试时生成；普通定位只输出最终最佳图。
        all_candidates_out = []
        candidates_to_render = candidates[:5] if debug_visualizations else []
        for idx, (s, rv, tv, ic, err, b3d, b2d) in enumerate(candidates_to_render):
            round_tag = f"salad_roma_r{idx}"
            round_result = _render_comparison(
                rv, tv, ic, b3d, b2d,
                known_points, camera_matrix, q_w, q_h, q_small,
                out, round_tag, q_small,
            )
            comp_img = round_result.get('comparison_image')
            reproj_img = round_result.get('reprojection_image')
            if comp_img:
                comp_img = comp_img.replace("projections/", "")
                comp_img = f"/projections/{comp_img}"
            if reproj_img:
                reproj_img = reproj_img.replace("projections/", "")
                reproj_img = f"/projections/{reproj_img}"
            all_candidates_out.append({
                'round': idx,
                'salad_similarity': round(s, 4),
                'pnp_inliers': ic,
                'translation': tv.flatten().tolist() if hasattr(tv, 'flatten') else tv,
                'comparison_image': comp_img,
                'reprojection_image': reproj_img,
                'is_best': idx == 0,
            })
        
        result = _render_comparison(
            best_rvec, best_tvec, best_inliers, best_3d, best_2d,
            known_points, camera_matrix, q_w, q_h, q_small,
            out, tag, q_small, all_pts=all_pts, all_col=all_col,
        )
        result['iter_history'] = iter_history
        result['total_rounds'] = len(candidates)
        result['all_candidates'] = all_candidates_out
        return result
    
    return {"success": False, "error": "SALAD+LightGlue PnP 失败", "tag": tag}


# ============================================================
#  LightGlue+PNP 单步优化：基于已有位姿再做稀疏匹配优化
# ============================================================

def refine_pose_with_roma(
    query_image_path: str,
    rvec, tvec,
    camera_matrix, q_w, q_h,
    all_pts, all_col,
    out_dir: str = "projections/localize",
    tag: str = "roma_refine",
) -> dict:
    """
    基于已有 PnP 位姿，用 LightGlue 匹配原图 vs 重投影图，
    再做 PnP → 评估 SALAD 相似度。
    
    返回: {success, rvec, tvec, inliers, salad_sim, comparison_image, ...}
    """
    from services.localizer import _POINT_INDEX, load_colmap
    
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    
    query_img = cv2.imread(query_image_path)
    if query_img is None:
        return {"success": False, "error": "Cannot read query image"}
    
    h_orig, w_orig = query_img.shape[:2]
    q_small = cv2.resize(query_img, (512, 512))
    
    # 渲染重投影图
    ref_proj = str(out / f"_refine_{tag}_proj.png")
    ref_proj_path, ref_coord = render_projection_image(
        all_pts, all_col, rvec, tvec, camera_matrix, q_w, q_h, ref_proj,
        include_coord_map=True,
    )
    if ref_proj_path is None:
        return {"success": False, "error": "Render failed"}
    
    ref_img = cv2.imread(ref_proj_path)
    if ref_img is None:
        return {"success": False, "error": "Read render failed"}
    
    # 初始 SALAD 相似度
    salad_sim = 0.0
    salad_model, salad_scale = _get_dinov2_model()
    if salad_model is not None:
        q_desc = _extract_multimodal_descriptor(salad_model, q_small, None, None, salad_scale)
        ref_desc = _extract_multimodal_descriptor(salad_model, ref_img, None, None, salad_scale)
        if q_desc is not None and ref_desc is not None:
            salad_sim = float(np.dot(q_desc, ref_desc) / (np.linalg.norm(q_desc) * np.linalg.norm(ref_desc) + 1e-8))
    
    log(f"  [REFINE] 初始 SALAD 相似度: {salad_sim:.4f}")
    
    # LightGlue 匹配原图 vs 重投影图
    kpts_q, kpts_proj, cert = _lightglue_match(q_small, ref_img, sample_num=3000)
    if len(kpts_q) < 10:
        if os.path.exists(ref_proj): os.remove(ref_proj)
        return {"success": False, "error": f"LightGlue matches too few: {len(kpts_q)}"}
    
    # 构建 3D-2D 匹配
    obj_pts, img_pts = _build_3d_2d_matches(kpts_q, kpts_proj, cert, ref_coord)
    log(f"  [REFINE] 3D-2D: {len(obj_pts)} 对")
    
    if len(obj_pts) < 4:
        if os.path.exists(ref_proj): os.remove(ref_proj)
        return {"success": False, "error": f"3D-2D too few: {len(obj_pts)}"}
    
    nr, nt, ni = _solve_pnp(obj_pts, img_pts, camera_matrix)
    if nr is None:
        if os.path.exists(ref_proj): os.remove(ref_proj)
        return {"success": False, "error": "PnP failed"}
    
    nic = len(ni) if ni is not None else len(obj_pts)
    log(f"  [REFINE] PnP: {nic}/{len(obj_pts)} 内点")
    
    # 重渲染评估新位姿的 SALAD 相似度
    new_proj = str(out / f"_refine_{tag}_new.png")
    new_proj_path, _ = render_projection_image(
        all_pts, all_col, nr, nt, camera_matrix, q_w, q_h, new_proj
    )
    new_sim = 0.0
    if new_proj_path and salad_model is not None:
        new_img = cv2.imread(new_proj_path)
        if new_img is not None:
            new_desc = _extract_multimodal_descriptor(salad_model, new_img, None, None, salad_scale)
            if new_desc is not None and q_desc is not None:
                new_sim = float(np.dot(new_desc, q_desc) / (np.linalg.norm(new_desc) * np.linalg.norm(q_desc) + 1e-8))
    
    log(f"  [REFINE] 新位姿 SALAD 相似度: {new_sim:.4f}")
    
    # 清理
    for f in [ref_proj, new_proj]:
        if os.path.exists(f): os.remove(f)
    
    return {
        "success": True,
        "rvec": nr,
        "tvec": nt,
        "inliers": nic,
        "salad_sim_before": round(salad_sim, 4),
        "salad_sim_after": round(new_sim, 4),
        "improved": new_sim > salad_sim,
    }


def _localize_from_tile_matches(
    tile_results, q_small, q_w, q_h, q_w_orig, q_h_orig,
    camera_matrix, all_pts, all_col, known_points,
    out, tag, cache_key, max_iterations,
):
    """Fallback: 用 SIFT tile 检索结果做 PnP（使用 KD-Tree 投影而非 RoMa）"""
    from services.localizer import _POINT_INDEX, _compute_pose_projection, _find_nearby_keypoints_vectorized, _solve_pnp as _sift_pnp
    
    poses, _, _, _ = _load_poses_and_offset()
    q_gray = cv2.cvtColor(q_small, cv2.COLOR_BGR2GRAY)
    q_kp, q_des = cv2.SIFT_create(nfeatures=3000).detectAndCompute(q_gray, None)
    if q_kp is None:
        return {"success": False, "error": "No SIFT features", "tag": tag}
    
    candidate_poses = []
    from services.localizer import _resolve_pose_from_tile
    seen_pose_idx = set()
    for n_matches, ti, view_type, tile_key in tile_results:
        pose = _resolve_pose_from_tile(tile_key)
        if pose is not None:
            pk = (round(pose['x'], 1), round(pose['y'], 1))
            if pk not in seen_pose_idx:
                seen_pose_idx.add(pk)
                candidate_poses.append(pose)
    
    # PnP on candidates
    best_inliers = 0
    best_rvec, best_tvec = None, None
    best_3d, best_2d = None, None
    pts_all = _POINT_INDEX["pts"]
    
    for pi, pose in enumerate(candidate_poses):
        result = _compute_pose_projection(pose, pts_all, camera_matrix, q_w, q_h, 50.0)
        if result is None:
            continue
        px_in, py_in, pts_3d_valid = result
        local_3d, local_2d, _ = _find_nearby_keypoints_vectorized(px_in, py_in, q_kp, 15, pts_3d_valid)
        if len(local_3d) < 4:
            continue
        rvec_i, tvec_i, inliers_i = _sift_pnp(
            np.array(local_3d, dtype=np.float64),
            np.array(local_2d, dtype=np.float64), camera_matrix
        )
        if rvec_i is not None:
            ic = len(inliers_i) if inliers_i is not None else len(local_3d)
            if ic > best_inliers:
                best_inliers, best_rvec, best_tvec = ic, rvec_i, tvec_i
                best_3d, best_2d = local_3d, local_2d
    
    if best_rvec is None:
        return {"success": False, "error": "PnP failed", "tag": tag}
    
    _PNP_CACHE[cache_key] = {
        'rvec': best_rvec, 'tvec': best_tvec, 'inliers': best_inliers,
        'best_3d': best_3d, 'best_2d': best_2d,
        'q_small': q_small, 'q_w': q_w, 'q_h': q_h,
        'camera_matrix': camera_matrix, 'known_points': known_points,
    }
    return _render_comparison(
        best_rvec, best_tvec, best_inliers, best_3d, best_2d,
        known_points, camera_matrix, q_w, q_h, q_small,
        out, tag, q_small,
    )


def _render_comparison(
    rvec, tvec, inlier_count, best_3d, best_2d,
    known_points, camera_matrix, q_w, q_h, q_small,
    out: Path, tag: str, query_img_small, all_pts=None, all_col=None,
) -> dict:
    """
    生成重投影图和双图对比。
    匹配连线：原图和重投影图之间的 LightGlue/SIFT 匹配点。
    """
    if all_pts is None or all_col is None:
        from services.localizer import get_point_cloud_arrays
        all_pts, all_col = get_point_cloud_arrays()
    
    # 1. 重投影
    proj_path = str(out / f"reprojection_{tag}.png")
    t0 = time.time()
    proj_path, coord_map = render_projection_image(all_pts, all_col, rvec, tvec, camera_matrix, q_w, q_h, proj_path)
    log(f"  重投影渲染耗时: {time.time()-t0:.1f}s")
    
    coord_path = None
    
    # 2. 双图对比：原图 SIFT vs 重投影图 SIFT
    comparison_path = None
    matched_points_out = []
    similarity_score = 0.0
    
    if proj_path:
        proj_img = cv2.imread(proj_path)
        if proj_img is not None:
            if len(proj_img.shape) == 3:
                p_gray = cv2.cvtColor(proj_img, cv2.COLOR_BGR2GRAY)
            else:
                p_gray = proj_img
            q_gray = cv2.cvtColor(query_img_small, cv2.COLOR_BGR2GRAY) if len(query_img_small.shape) == 3 else query_img_small
            
            sift = cv2.SIFT_create(nfeatures=2000)
            kp1, des1 = sift.detectAndCompute(q_gray, None)
            kp2, des2 = sift.detectAndCompute(p_gray, None)
            
            if des1 is not None and des2 is not None and len(des1) > 5 and len(des2) > 5:
                flann = cv2.FlannBasedMatcher(dict(algorithm=1, trees=5), dict(checks=50))
                knn = flann.knnMatch(des1, des2, k=2)
                
                good_matches = []
                for pair in knn:
                    if len(pair) == 2:
                        m, n = pair[0], pair[1]
                        if m.distance < 0.75 * n.distance:
                            good_matches.append(m)
                
                if good_matches:
                    good_matches.sort(key=lambda x: x.distance)
                    display_matches = good_matches[:20]
                    avg_dist = sum(m.distance for m in good_matches) / len(good_matches)
                    similarity_score = len(good_matches) * (1.0 - min(avg_dist / 500.0, 1.0))
                    
                    h = max(q_h, proj_img.shape[0])
                    w = q_w + proj_img.shape[1]
                    canvas = np.zeros((h, w, 3), dtype=np.uint8)
                    canvas[:q_h, :q_w] = query_img_small
                    canvas[:proj_img.shape[0], q_w:] = proj_img
                    
                    colors = [
                        (255,0,0),(0,255,0),(0,0,255),(255,255,0),(255,0,255),
                        (0,255,255),(128,0,128),(255,128,0),(0,128,255),(128,255,0),
                    ]
                    for i, m in enumerate(display_matches):
                        x1, y1 = int(kp1[m.queryIdx].pt[0]), int(kp1[m.queryIdx].pt[1])
                        x2, y2 = int(kp2[m.trainIdx].pt[0]) + q_w, int(kp2[m.trainIdx].pt[1])
                        c = colors[i % len(colors)]
                        cv2.circle(canvas, (x1, y1), 5, c, -1)
                        cv2.circle(canvas, (x2, y2), 5, c, -1)
                        cv2.line(canvas, (x1, y1), (x2, y2), c, 1)
                        matched_points_out.append({
                            "x1": x1, "y1": y1,
                            "x2": x2 - q_w, "y2": y2,
                            "color": list(c),
                            "distance": float(m.distance),
                        })
                    
                    comparison_path = str(out / f"comparison_{tag}.png")
                    cv2.imwrite(comparison_path, canvas)
                    log(f"✅ 双图对比: {comparison_path} ({len(display_matches)}个匹配点, 相似度={similarity_score:.1f})")
    
    if comparison_path is None and proj_path:
        # 纯对比图（无匹配）
        h = max(q_h, cv2.imread(proj_path).shape[0])
        w = q_w + cv2.imread(proj_path).shape[1]
        canvas = np.zeros((h, w, 3), dtype=np.uint8)
        canvas[:q_h, :q_w] = query_img_small
        canvas[:cv2.imread(proj_path).shape[0], q_w:] = cv2.imread(proj_path)
        comparison_path = str(out / f"comparison_{tag}.png")
        cv2.imwrite(comparison_path, canvas)
    
    rmat, _ = cv2.Rodrigues(rvec)
    quat = _rotation_matrix_to_quaternion(rmat)
    
    proj_rel = os.path.relpath(proj_path, Path.cwd()) if proj_path else None
    comp_rel = os.path.relpath(comparison_path, Path.cwd()) if comparison_path else None
    
    return {
        "success": True,
        "tag": tag,
        "feature_method": "salad_roma",
        "match_method": "dense_pnp",
        "pose": {
            "quaternion": [float(q) for q in quat],
            "translation": tvec.flatten().tolist(),
        },
        "inliers": int(inlier_count),
        "total_3d_points": len(best_3d) if best_3d is not None else 0,
        "reprojection_image": proj_rel,
        "comparison_image": comp_rel,
        "coord_map": coord_path,
        "matched_points": matched_points_out,
        "similarity_score": round(similarity_score, 1),
    }
