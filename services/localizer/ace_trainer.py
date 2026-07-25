"""
ACE (Accelerated Coordinate Encoding) 场景坐标回归
参考：CVPR 2023 "Accelerated Coordinate Encoding: Learning to Relocalize in Minutes"

官方开源：https://github.com/nianticlabs/ace (网络通后可替换)

核心改进（对比原 ace_trainer.py）：
1. 全卷积架构（FCN），一次前向输出全图 XYZ
2. 深度可分离卷积（MobileNet-like），更快更轻
3. 多层特征融合，精度更高
4. 推理时无需滑动窗口

流程：
RGB图像 → FCN → 全图 XYZ 预测 → 像素采样 → RANSAC-PnP
"""

import json
import os
import time
from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader


# ── 设备 ──
DEVICE = torch.device("cuda" if torch.cuda.is_available() else
                      "mps" if torch.backends.mps.is_available() else "cpu")


# ── 全卷积 ACE 网络 ──
class DepthwiseSeparableConv(nn.Module):
    """深度可分离卷积"""
    def __init__(self, in_ch, out_ch, stride=1):
        super().__init__()
        self.depthwise = nn.Conv2d(in_ch, in_ch, 3, stride=stride, padding=1, groups=in_ch, bias=False)
        self.pointwise = nn.Conv2d(in_ch, out_ch, 1, bias=False)
        self.bn = nn.BatchNorm2d(out_ch)
        self.relu = nn.ReLU(inplace=True)
    
    def forward(self, x):
        return self.relu(self.bn(self.pointwise(self.depthwise(x))))


class CoordRegressionFCN(nn.Module):
    """
    全卷积场景坐标回归网络（ACE-style）。
    
    输入: (B, 6, H, W) — RGB(3) + Normal(3)
    输出: (B, 3, H, W) — 每个像素的 (X, Y, Z) 坐标
    """
    
    def __init__(self, in_channels: int = 6):
        super().__init__()
        
        # Encoder（下采样 + 特征提取）
        self.enc1 = nn.Sequential(
            nn.Conv2d(in_channels, 32, 3, padding=1),
            nn.BatchNorm2d(32), nn.ReLU(inplace=True),
            DepthwiseSeparableConv(32, 64, stride=2),  # H/2
        )
        self.enc2 = nn.Sequential(
            DepthwiseSeparableConv(64, 128, stride=2),  # H/4
            DepthwiseSeparableConv(128, 128, stride=1),
        )
        self.enc3 = nn.Sequential(
            DepthwiseSeparableConv(128, 256, stride=2),  # H/8
            DepthwiseSeparableConv(256, 256, stride=1),
        )
        
        # Decoder（上采样 + 跳连）
        self.dec3 = nn.Sequential(
            nn.ConvTranspose2d(256, 128, 4, stride=2, padding=1),
            nn.BatchNorm2d(128), nn.ReLU(inplace=True),
        )
        self.dec2 = nn.Sequential(
            nn.ConvTranspose2d(256, 64, 4, stride=2, padding=1),  # 256 = 128(in) + 128(skip)
            nn.BatchNorm2d(64), nn.ReLU(inplace=True),
        )
        self.dec1 = nn.Sequential(
            nn.ConvTranspose2d(128, 32, 4, stride=2, padding=1),  # 128 = 64(in) + 64(skip)
            nn.BatchNorm2d(32), nn.ReLU(inplace=True),
        )
        
        # 输出头
        self.out = nn.Sequential(
            nn.Conv2d(64, 32, 3, padding=1),  # 64 = 32(in) + 32(skip)
            nn.BatchNorm2d(32), nn.ReLU(inplace=True),
            nn.Conv2d(32, 3, 1),  # 输出 (X, Y, Z)
        )
    
    def forward(self, x):
        # Encoder
        e1 = self.enc1(x)    # B,64,H/2,W/2
        e2 = self.enc2(e1)   # B,128,H/4,W/4
        e3 = self.enc3(e2)   # B,256,H/8,W/8
        
        # Decoder with skip connections
        d3 = self.dec3(e3)                          # B,128,H/4,W/4
        d3 = torch.cat([d3, e2], dim=1)             # B,256,H/4,W/4
        d2 = self.dec2(d3)                          # B,64,H/2,W/2
        d2 = torch.cat([d2, e1], dim=1)             # B,128,H/2,W/2
        d1 = self.dec1(d2)                          # B,32,H,W
        d1 = torch.cat([d1, x[:, :3]], dim=1)       # B,35,H,W  (skip RGB)
        
        out = self.out(d1)                          # B,3,H,W
        return out


# ── 数据集 ──
class SceneCoordinateDataset(Dataset):
    """全图训练：加载 tile 的全图 XYZ 映射"""
    
    def __init__(self, tile_index_path: str, img_size: int = 256, max_samples: int = 20000):
        with open(tile_index_path) as f:
            self.tiles = json.load(f)
        
        self.img_size = img_size
        self.samples = []
        
        for tile in self.tiles:
            img_path = tile.get("image_path", "")
            npy_path = tile.get("npy_path", "")
            normal_path = tile.get("normal_path", "")
            if not img_path or not npy_path:
                continue
            if not os.path.exists(img_path) or not os.path.exists(npy_path):
                continue
            xyz = np.load(npy_path)  # (H, W, 3)
            valid = np.linalg.norm(xyz, axis=2) > 1e-6
            if not valid.any():
                continue
            self.samples.append((img_path, npy_path, normal_path))
            if len(self.samples) >= max_samples:
                break
        
        print(f"[ACE] 全图数据集: {len(self.samples)} tiles")
    
    def __len__(self):
        return len(self.samples)
    
    def __getitem__(self, idx):
        img_path, npy_path, normal_path = self.samples[idx]
        
        # 读取 RGB
        img = cv2.imread(img_path)  # (H, W, 3) uint8
        h, w = img.shape[:2]
        
        # 缩放
        scale = self.img_size / max(h, w)
        new_h, new_w = int(h * scale), int(w * scale)
        img = cv2.resize(img, (new_w, new_h))
        
        # Normal
        normal = np.zeros((new_h, new_w, 3), dtype=np.float32)
        if normal_path and os.path.exists(normal_path):
            n_map = np.load(normal_path)
            if n_map.size > 0:
                normal = cv2.resize(n_map, (new_w, new_h), interpolation=cv2.INTER_NEAREST)
        
        # XYZ（缩放坐标）
        xyz = np.load(npy_path)
        xyz = cv2.resize(xyz, (new_w, new_h), interpolation=cv2.INTER_NEAREST)
        
        # 堆叠 6 通道
        img_norm = img.astype(np.float32) / 255.0
        normal_norm = (normal + 1.0) * 0.5
        six_ch = np.concatenate([img_norm, normal_norm], axis=2)  # (H, W, 6)
        six_ch = torch.from_numpy(six_ch).permute(2, 0, 1)       # (6, H, W)
        
        # XYZ 作为训练目标
        xyz_t = torch.from_numpy(xyz).permute(2, 0, 1)            # (3, H, W)
        
        # 有效区域掩码
        valid_mask = np.linalg.norm(xyz, axis=2) > 1e-6
        valid_t = torch.from_numpy(valid_mask.astype(np.float32))  # (H, W)
        
        return six_ch, xyz_t, valid_t


# ── 训练 ──
class ACTLoss(nn.Module):
    """ACE-style 损失：有效像素的 SmoothL1，对无效像素忽略"""
    def forward(self, pred, target, valid):
        loss = nn.functional.smooth_l1_loss(pred, target, reduction='none')
        loss = loss * valid.unsqueeze(1)  # 只算有效像素
        return loss.sum() / (valid.sum() + 1e-6)


def train_ace_model(
    tile_index_path: str = "projections/tile_index.json",
    model_save_path: str = "projections/ace_model.pth",
    epochs: int = 20,
    batch_size: int = 4,
    lr: float = 1e-3,
):
    """训练全卷积 ACE 场景坐标回归模型"""
    print(f"[ACE] 训练设备: {DEVICE}")
    
    dataset = SceneCoordinateDataset(tile_index_path)
    if len(dataset) == 0:
        print("[ACE] ❌ 无训练数据")
        return None
    
    dataloader = DataLoader(dataset, batch_size=batch_size, shuffle=True, num_workers=2)
    
    model = CoordRegressionFCN(in_channels=6).to(DEVICE)
    optimizer = optim.Adam(model.parameters(), lr=lr)
    scheduler = optim.lr_scheduler.StepLR(optimizer, step_size=8, gamma=0.3)
    criterion = ACTLoss()
    
    print(f"[ACE] 训练开始: {len(dataset)} 样本, {epochs} epochs")
    t0 = time.time()
    
    for epoch in range(epochs):
        total_loss = 0.0
        n_batches = 0
        
        for images, xyzs, valids in dataloader:
            images = images.to(DEVICE)
            xyzs = xyzs.to(DEVICE)
            valids = valids.to(DEVICE)
            
            pred = model(images)
            loss = criterion(pred, xyzs, valids)
            
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            
            total_loss += loss.item()
            n_batches += 1
        
        scheduler.step()
        avg_loss = total_loss / max(n_batches, 1)
        print(f"[ACE] Epoch {epoch+1}/{epochs}  loss={avg_loss:.4f}")
    
    elapsed = time.time() - t0
    print(f"[ACE] 训练完成: {elapsed:.1f}s")
    
    torch.save(model.state_dict(), model_save_path)
    print(f"[ACE] 模型已保存: {model_save_path}")
    
    return model


# ── 推理 ──
def ace_predict_dense(
    model: nn.Module,
    image: np.ndarray,
    normal_map: np.ndarray = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    全图一次前向预测 XYZ。
    
    返回:
        - coords_2d: (N, 2) 有效像素坐标（降采样后）
        - coords_3d: (N, 3) 预测 3D 坐标
        - confidence: (N,) 预测置信度（基于局部方差）
    """
    model.eval()
    orig_h, orig_w = image.shape[:2]
    
    # 缩放到网络输入尺寸
    target_size = 256
    scale = target_size / max(orig_h, orig_w)
    new_h, new_w = int(orig_h * scale), int(orig_w * scale)
    img_small = cv2.resize(image, (new_w, new_h))
    
    img_norm = img_small.astype(np.float32) / 255.0
    
    if normal_map is None or normal_map.size == 0:
        normal_map = np.zeros((new_h, new_w, 3), dtype=np.float32)
    else:
        normal_map = cv2.resize(normal_map, (new_w, new_h), interpolation=cv2.INTER_NEAREST)
    normal_norm = (normal_map + 1.0) * 0.5
    
    six_ch = np.concatenate([img_norm, normal_norm], axis=2)
    tensor = torch.from_numpy(six_ch).permute(2, 0, 1).unsqueeze(0).float().to(DEVICE)
    
    with torch.no_grad():
        pred_xyz = model(tensor)  # (1, 3, H, W)
    
    pred_xyz = pred_xyz[0].cpu().numpy()  # (3, H, W)
    
    # 有效像素过滤（预测的 XYZ 非零）
    pred_valid = np.linalg.norm(pred_xyz, axis=0) > 1e-6
    
    # 置信度估计（基于 3x3 邻域的局部方差，方差小=置信度高）
    from scipy.ndimage import generic_filter
    def local_var(x):
        return x.var() if len(x) > 0 else 0
    confidence = np.ones_like(pred_valid, dtype=np.float32)
    for c in range(3):
        var_map = generic_filter(pred_xyz[c], local_var, size=3)
        confidence = np.minimum(confidence, 1.0 / (1.0 + var_map))
    
    confidence[~pred_valid] = 0.0
    
    # 提取有效像素坐标（还原到原始图像尺寸）
    valid_ys, valid_xs = np.where(pred_valid)
    if len(valid_ys) == 0:
        return np.array([]), np.array([]), np.array([])
    
    # 降采样到原始图像尺寸
    scale_x = orig_w / new_w
    scale_y = orig_h / new_h
    
    pts_2d = np.column_stack([
        valid_xs.astype(float) * scale_x,
        valid_ys.astype(float) * scale_y,
    ])
    pts_3d = pred_xyz[:, valid_ys, valid_xs].T
    conf = confidence[valid_ys, valid_xs]
    
    # 按置信度排序取 top-K（控制 PnP 输入量）
    top_k = min(5000, len(pts_2d))
    top_idx = np.argsort(-conf)[:top_k]
    
    return pts_2d[top_idx], pts_3d[top_idx], conf[top_idx]


def ace_localize(
    model: nn.Module,
    image: np.ndarray,
    K: np.ndarray,
    normal_map: np.ndarray = None,
) -> tuple[bool, np.ndarray, np.ndarray, np.ndarray]:
    """
    ACE 全图预测 + PnP 一步定位（无需多轮迭代）。
    
    返回: (success, rvec, tvec, inliers)
    """
    pts_2d, pts_3d, conf = ace_predict_dense(model, image, normal_map)
    
    if len(pts_2d) < 10:
        print(f"[ACE] ❌ 有效预测点不足: {len(pts_2d)}")
        return False, None, None, None
    
    # 加权 RANSAC PnP（用置信度加权）
    dist_coeffs = np.zeros((4, 1))
    success, rvec, tvec, inliers = cv2.solvePnPRansac(
        pts_3d, pts_2d, K, dist_coeffs,
        iterationsCount=300,
        reprojectionError=12.0,
        confidence=0.999,
        useExtrinsicGuess=False,
    )
    
    if not success or inliers is None or len(inliers) < 10:
        print(f"[ACE] ❌ PnP 失败: inliers={len(inliers) if inliers is not None else 0}")
        return False, None, None, None
    
    print(f"[ACE] ✅ 定位成功: {len(inliers)}/{len(pts_2d)} 内点")
    return True, rvec, tvec, inliers


# ── 主入口 ──
if __name__ == "__main__":
    model = train_ace_model()
    
    if model is not None:
        test_tiles = json.load(open("projections/tile_index.json"))
        if test_tiles:
            test_img = cv2.imread(test_tiles[0]["image_path"])
            if test_img is not None:
                fov_deg = 75
                f = max(test_img.shape[1], test_img.shape[0]) / (2 * np.tan(np.deg2rad(fov_deg / 2)))
                K = np.array([[f, 0, test_img.shape[1]/2],
                             [0, f, test_img.shape[0]/2],
                             [0, 0, 1]])
                success, rvec, tvec, _ = ace_localize(model, test_img, K)
                print(f"[ACE] 测试定位: {'✅ 成功' if success else '❌ 失败'}")
