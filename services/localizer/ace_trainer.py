"""
ACE (Accelerated Coordinate Encoding) 场景坐标回归
使用官方 Nianticlabs ACE 网络架构 (CVPR 2023)

官方代码: https://github.com/nianticlabs/ace

修改：
1. 输入通道 1→6（支持 RGB+Normal）
2. 其余网络结构保持与官方一致（Encoder + Head MLP + 1x1 convs）
3. 输出 1/8 分辨率（subsampled），采样后 PnP
"""

import json
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader

# 确保 ace 模块可导入（当从 api/routes/ 调用时）
_ace_dir = Path(__file__).parent / "ace"
if str(_ace_dir.parent) not in sys.path:
    sys.path.insert(0, str(_ace_dir.parent))
from ace.ace_network import Encoder, Head


# ── 设备 ──
DEVICE = torch.device("cuda" if torch.cuda.is_available() else
                      "mps" if torch.backends.mps.is_available() else "cpu")


# ── 自定义 Encoder（支持 6 通道输入） ──
class Encoder6Ch(Encoder):
    """将官方 Encoder 的第一层从 1→32 改为 6→32，支持 RGB+Normal 输入"""
    def __init__(self, out_channels=512):
        super(Encoder6Ch, self).__init__(out_channels)
        self.conv1 = nn.Conv2d(6, 32, 3, 1, 1)  # 6通道: RGB(3) + Normal(3)


# ── 完整 ACE Regressor ──
class ACERegressor(nn.Module):
    """
    官方 ACE Regressor 架构。
    - Encoder: FCN 下采样 8x
    - Head: MLP (1x1 convs) 预测场景坐标
    - 输出: (B, 3, H/8, W/8)
    """
    OUTPUT_SUBSAMPLE = 8

    def __init__(self, mean, num_head_blocks=1, use_homogeneous=False, num_encoder_features=256):
        super().__init__()
        self.feature_dim = num_encoder_features
        self.encoder = Encoder6Ch(out_channels=self.feature_dim)
        self.heads = Head(mean, num_head_blocks, use_homogeneous, in_channels=self.feature_dim)

    def forward(self, x):
        features = self.encoder(x)
        coords = self.heads(features)
        return coords


# ── 数据集（全图，与官方 ACE 一致） ──
class SceneCoordinateDataset(Dataset):
    """加载全图 RGB+Normal+XYZ，训练时下采样到合适尺寸"""
    
    def __init__(self, tile_index_path: str, max_samples: int = 500):
        with open(tile_index_path) as f:
            self.tiles = json.load(f)
        self.samples = [t for t in self.tiles if t.get("accepted", False)
                        and t.get("image_path") and os.path.exists(t["image_path"])
                        and t.get("npy_path") and os.path.exists(t["npy_path"])]
        # 限制样本数
        if len(self.samples) > max_samples:
            self.samples = self.samples[:max_samples]
        print(f"[ACE] 数据集: {len(self.samples)} tiles")
    
    def __len__(self):
        return len(self.samples)
    
    def __getitem__(self, idx):
        tile = self.samples[idx]
        img = cv2.imread(tile["image_path"])
        h, w = img.shape[:2]
        
        # 缩放到网络输入尺寸（确保 8 的倍数）
        target_h = (h // 32) * 32
        target_w = (w // 32) * 32
        img = cv2.resize(img, (target_w, target_h))
        
        # RGB to float [0,1]
        rgb = img.astype(np.float32) / 255.0
        
        # Normal
        normal = np.zeros((target_h, target_w, 3), dtype=np.float32)
        normal_path = tile.get("normal_path", "")
        if normal_path and os.path.exists(normal_path):
            nm = np.load(normal_path)
            if nm.size > 0:
                normal = cv2.resize(nm, (target_w, target_h), interpolation=cv2.INTER_NEAREST)
        normal = (normal + 1.0) * 0.5
        
        # 6ch input
        six_ch = np.concatenate([rgb, normal], axis=2)  # (H, W, 6)
        six_ch = torch.from_numpy(six_ch).permute(2, 0, 1).float()
        
        # XYZ target（1/8 分辨率）
        xyz = np.load(tile["npy_path"])
        xyz = cv2.resize(xyz, (target_w, target_h), interpolation=cv2.INTER_NEAREST)
        # 降采样到 1/8
        xyz_lr = xyz[::8, ::8]  # (H/8, W/8, 3)
        # 有效像素掩码
        valid = np.linalg.norm(xyz_lr, axis=2) > 1e-6
        xyz_t = torch.from_numpy(xyz_lr).permute(2, 0, 1).float()  # (3, H/8, W/8)
        valid_t = torch.from_numpy(valid.astype(np.float32))        # (H/8, W/8)
        
        return six_ch, xyz_t, valid_t


# ── 损失函数（ACE-style，只算有效像素） ──
class ACTLoss(nn.Module):
    def forward(self, pred, target, valid):
        loss = nn.functional.smooth_l1_loss(pred, target, reduction='none')
        loss = loss * valid.unsqueeze(1)
        return loss.sum() / (valid.sum() * 3 + 1e-6)


# ── 训练 ──
def train_ace_model(
    tile_index_path: str = "projections/tile_index.json",
    model_save_path: str = "projections/ace_model.pth",
    epochs: int = 100,
    batch_size: int = 2,
    lr: float = 1e-3,
):
    print(f"[ACE] 训练设备: {DEVICE}")
    print(f"[ACE] 使用官方 ACE 网络架构 (Encoder6Ch + Head)")
    
    dataset = SceneCoordinateDataset(tile_index_path)
    if len(dataset) == 0:
        print("[ACE] ❌ 无训练数据")
        return None
    
    dataloader = DataLoader(dataset, batch_size=batch_size, shuffle=True, num_workers=2)
    
    # 计算场景坐标均值
    means = []
    for i in range(min(50, len(dataset))):
        _, xyz, valid = dataset[i]
        if valid.sum() > 0:
            means.append(xyz[:, valid > 0.5].mean(dim=1))
    mean = torch.stack(means).mean(dim=0) if means else torch.zeros(3)
    print(f"[ACE] 坐标均值: {mean}")
    
    model = ACERegressor(mean=mean, num_head_blocks=1, use_homogeneous=False).to(DEVICE)
    optimizer = optim.Adam(model.parameters(), lr=lr)
    scheduler = optim.lr_scheduler.StepLR(optimizer, step_size=10, gamma=0.5)
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
def ace_predict_dense(model, image, normal_map=None):
    """
    全图一次前向预测 XYZ。
    保持原分辨率确保精度，降采样 PnP 点数量保速度。
    返回: (pts_2d, pts_3d, confidence)
    """
    model.eval()
    orig_h, orig_w = image.shape[:2]
    
    # 对齐到 32 的倍数（网络下采样 8x 要求）
    target_h = (orig_h // 32) * 32
    target_w = (orig_w // 32) * 32
    if target_h < 32: target_h = 32
    if target_w < 32: target_w = 32
    
    img = cv2.resize(image, (target_w, target_h))
    rgb = img.astype(np.float32) / 255.0
    
    if normal_map is None or normal_map.size == 0:
        normal = np.zeros((target_h, target_w, 3), dtype=np.float32)
    else:
        normal = cv2.resize(normal_map, (target_w, target_h), interpolation=cv2.INTER_NEAREST)
    normal = (normal + 1.0) * 0.5
    
    six_ch = np.concatenate([rgb, normal], axis=2)
    tensor = torch.from_numpy(six_ch).permute(2, 0, 1).unsqueeze(0).float().to(DEVICE)
    
    with torch.no_grad():
        pred = model(tensor)  # (1, 3, H/8, W/8)
    
    pred = pred[0].cpu().numpy()
    valid = np.linalg.norm(pred, axis=0) > 1e-6
    
    # 还原到原始像素坐标
    scale_x = orig_w / target_w
    scale_y = orig_h / target_h
    
    ys, xs = np.where(valid)
    if len(ys) == 0:
        return np.array([]), np.array([]), np.array([])
    
    pts_2d = np.column_stack([xs.astype(float) * 8 * scale_x + 4,
                              ys.astype(float) * 8 * scale_y + 4])
    pts_3d = pred[:, ys, xs].T
    
    # 网格降采样到 2000 点（均匀覆盖，避免随机性）
    if len(pts_2d) > 2000:
        # 按网格采样：把图像分成网格，每个网格取中心点
        grid_size = int(np.sqrt(len(pts_2d) / 2000)) + 1
        grid_coords = (pts_2d / grid_size).astype(int)
        _, unique_idx = np.unique(grid_coords, axis=0, return_index=True)
        unique_idx = np.sort(unique_idx)[:2000]
        pts_2d, pts_3d = pts_2d[unique_idx], pts_3d[unique_idx]
    
    conf = np.ones(len(pts_2d))
    return pts_2d, pts_3d, conf


def ace_localize(model, image, K, normal_map=None):
    pts_2d, pts_3d, _ = ace_predict_dense(model, image, normal_map)
    if len(pts_2d) < 10:
        print(f"[ACE] ❌ 有效点不足: {len(pts_2d)}")
        return False, None, None, None
    
    dist = np.zeros((4, 1))
    success, rvec, tvec, inliers = cv2.solvePnPRansac(
        pts_3d, pts_2d, K, dist,
        iterationsCount=500, reprojectionError=20.0, confidence=0.95)
    
    if not success or len(pts_2d[inliers.flatten()] if inliers is not None else pts_2d) < 6:
        return False, None, None, None
    inlier_count = len(inliers) if inliers is not None else len(pts_2d)
    print(f"[ACE] ✅ {inlier_count}/{len(pts_2d)} 内点")
    return True, rvec, tvec, inliers


if __name__ == "__main__":
    model = train_ace_model()
    if model:
        test_tiles = json.load(open("projections/tile_index.json"))
        if test_tiles:
            img = cv2.imread(test_tiles[0]["image_path"])
            if img is not None:
                fov_deg = 75
                f = max(img.shape[1], img.shape[0]) / (2 * np.tan(np.deg2rad(fov_deg / 2)))
                K = np.array([[f, 0, img.shape[1]/2], [0, f, img.shape[0]/2], [0, 0, 1]])
                ok, _, _, _ = ace_localize(model, img, K)
                print(f"[ACE] 测试: {'✅' if ok else '❌'}")
