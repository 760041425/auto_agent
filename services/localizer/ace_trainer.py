"""
ACE (Accelerated Coordinate Encoding) 场景坐标回归
参考：CVPR 2023 "Accelerated Coordinate Encoding: Learning to Relocalize in Minutes"

流程：
1. 从渲染 tiles 提取 2D-3D 对应点作为训练数据
2. 训练轻量 CNN + MLP 预测 3D 坐标
3. 推理时单次前向 + RANSAC-PnP
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


# ── 数据集 ──
class SceneCoordinateDataset(Dataset):
    """从渲染 tiles 加载 2D-3D 对应关系"""

    def __init__(self, tile_index_path: str, patch_size: int = 32, max_samples: int = 50000):
        """
        Args:
            tile_index_path: tile_index.json 路径
            patch_size: 每个训练样本的 patch 大小（像素）
            max_samples: 最多采样点数
        """
        with open(tile_index_path) as f:
            self.tiles = json.load(f)

        self.patch_size = patch_size
        self.samples = []  # [(img_path, npy_path, u, v, x, y, z), ...]

        total_pixels = 0
        for tile in self.tiles:
            img_path = tile.get("image_path", "")
            npy_path = tile.get("npy_path", "")
            if not img_path or not npy_path:
                continue
            if not os.path.exists(img_path) or not os.path.exists(npy_path):
                continue

            # 读取 XYZ
            xyz = np.load(npy_path)  # (H, W, 3)
            valid = np.linalg.norm(xyz, axis=2) > 1e-6
            valid_indices = np.where(valid)

            if len(valid_indices[0]) == 0:
                continue

            total_pixels += len(valid_indices[0])

            # 随机采样（限制每个 tile 的采样数避免不均衡）
            n_points = min(len(valid_indices[0]), max_samples // len(self.tiles) + 1)
            chosen = np.random.choice(len(valid_indices[0]), n_points, replace=False)

            for idx in chosen:
                v, u = valid_indices[0][idx], valid_indices[1][idx]
                x, y, z = xyz[v, u]
                self.samples.append((img_path, npy_path, u, v, float(x), float(y), float(z)))

            if len(self.samples) >= max_samples:
                break

        print(f"[ACE] 数据集: {len(self.tiles)} tiles, {len(self.samples)} 样本, {total_pixels} 总像素")

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        img_path, npy_path, u, v, x, y, z = self.samples[idx]

        # 读取图像 patch
        img = cv2.imread(img_path)
        h, w = img.shape[:2]

        # 提取以 (u,v) 为中心的 patch，边界处 clamp
        ps = self.patch_size // 2
        u_start = max(0, u - ps)
        u_end = min(w, u + ps)
        v_start = max(0, v - ps)
        v_end = min(h, v + ps)

        patch = img[v_start:v_end, u_start:u_end]
        if patch.shape[0] < self.patch_size or patch.shape[1] < self.patch_size:
            # padding
            pad_h = self.patch_size - patch.shape[0]
            pad_w = self.patch_size - patch.shape[1]
            patch = np.pad(patch, ((0, pad_h), (0, pad_w), (0, 0)), mode='edge')

        # 归一化到 [0,1]
        patch = patch.astype(np.float32) / 255.0
        # (H, W, C) → (C, H, W)
        patch = torch.from_numpy(patch).permute(2, 0, 1)

        # 3D 坐标
        coord = torch.tensor([x, y, z], dtype=torch.float32)

        return patch, coord


# ── ACE 网络 ──
class CoordRegression(nn.Module):
    """轻量级场景坐标回归网络"""

    def __init__(self, in_channels: int = 3):
        super().__init__()
        # CNN 特征提取器
        self.cnn = nn.Sequential(
            nn.Conv2d(in_channels, 32, 5, padding=2),
            nn.ReLU(),
            nn.MaxPool2d(2),  # 16x16
            nn.Conv2d(32, 64, 3, padding=1),
            nn.ReLU(),
            nn.MaxPool2d(2),  # 8x8
            nn.Conv2d(64, 128, 3, padding=1),
            nn.ReLU(),
            nn.AdaptiveAvgPool2d(1),  # 全局池化
        )

        # MLP 坐标回归头
        self.mlp = nn.Sequential(
            nn.Linear(128, 256),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(256, 128),
            nn.ReLU(),
            nn.Linear(128, 3),  # 输出 (X, Y, Z)
        )

    def forward(self, x):
        feat = self.cnn(x)  # (B, 128, 1, 1)
        feat = feat.view(feat.size(0), -1)  # (B, 128)
        coords = self.mlp(feat)  # (B, 3)
        return coords


# ── 训练 ──
def train_ace_model(
    tile_index_path: str = "projections/tile_index.json",
    model_save_path: str = "projections/ace_model.pth",
    epochs: int = 30,
    batch_size: int = 256,
    lr: float = 1e-3,
):
    """训练 ACE 场景坐标回归模型"""
    print(f"[ACE] 训练设备: {DEVICE}")

    dataset = SceneCoordinateDataset(tile_index_path)
    if len(dataset) == 0:
        print("[ACE] ❌ 无训练数据")
        return None

    dataloader = DataLoader(dataset, batch_size=batch_size, shuffle=True, num_workers=2)

    model = CoordRegression().to(DEVICE)
    optimizer = optim.Adam(model.parameters(), lr=lr)
    scheduler = optim.lr_scheduler.StepLR(optimizer, step_size=10, gamma=0.5)
    criterion = nn.SmoothL1Loss()  # Huber loss 对异常值更鲁棒

    print(f"[ACE] 训练开始: {len(dataset)} 样本, {epochs} epochs")
    t0 = time.time()

    for epoch in range(epochs):
        total_loss = 0.0
        n_batches = 0

        for patches, coords in dataloader:
            patches = patches.to(DEVICE)
            coords = coords.to(DEVICE)

            pred = model(patches)
            loss = criterion(pred, coords)

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

            total_loss += loss.item()
            n_batches += 1

        scheduler.step()
        avg_loss = total_loss / n_batches
        print(f"[ACE] Epoch {epoch+1}/{epochs}  loss={avg_loss:.4f}  lr={scheduler.get_last_lr()[0]:.2e}")

    elapsed = time.time() - t0
    print(f"[ACE] 训练完成: {elapsed:.1f}s")

    # 保存模型
    torch.save(model.state_dict(), model_save_path)
    print(f"[ACE] 模型已保存: {model_save_path}")

    return model


# ── 推理 ──
def ace_predict_coords(
    model: nn.Module,
    image: np.ndarray,
    stride: int = 32,
    patch_size: int = 32,
) -> tuple[np.ndarray, np.ndarray]:
    """
    用 ACE 模型预测图像中每个像素的 3D 坐标。

    Args:
        model: 训练好的 CoordRegression 模型
        image: (H, W, 3) RGB/BGR uint8 图像
        stride: 滑动窗口步长
        patch_size: 输入 patch 大小

    Returns:
        (coords_2d, coords_3d)
        - coords_2d: (N, 2) 像素坐标
        - coords_3d: (N, 3) 预测的 3D 世界坐标
    """
    model.eval()
    h, w = image.shape[:2]

    img_float = image.astype(np.float32) / 255.0
    ps = patch_size // 2

    coords_2d_list = []
    coords_3d_list = []

    with torch.no_grad():
        for v in range(ps, h - ps, stride):
            for u in range(ps, w - ps, stride):
                patch = img_float[v-ps:v+ps, u-ps:u+ps]
                if patch.shape[0] < patch_size or patch.shape[1] < patch_size:
                    continue

                tensor = torch.from_numpy(patch).permute(2, 0, 1).unsqueeze(0).float().to(DEVICE)
                pred_xyz = model(tensor).cpu().numpy().flatten()

                # 只保留非零预测
                if np.linalg.norm(pred_xyz) > 1e-6:
                    coords_2d_list.append([u, v])
                    coords_3d_list.append(pred_xyz)

    if len(coords_2d_list) == 0:
        return np.array([]), np.array([])

    return np.array(coords_2d_list), np.array(coords_3d_list)


def ace_localize(
    model: nn.Module,
    image: np.ndarray,
    K: np.ndarray,
    stride: int = 32,
) -> tuple[bool, np.ndarray, np.ndarray, np.ndarray]:
    """
    用 ACE 预测 + PnP 一步定位。

    返回: (success, rvec, tvec, inliers_mask)
    """
    pts_2d, pts_3d = ace_predict_coords(model, image, stride=stride)

    if len(pts_2d) < 6:
        print(f"[ACE] ❌ 有效预测点不足: {len(pts_2d)}")
        return False, None, None, None

    # RANSAC PnP
    dist_coeffs = np.zeros((4, 1))
    success, rvec, tvec, inliers = cv2.solvePnPRansac(
        pts_3d, pts_2d, K, dist_coeffs,
        iterationsCount=200,
        reprojectionError=8.0,
        confidence=0.99,
    )

    if not success or inliers is None or len(inliers) < 6:
        return False, None, None, None

    inlier_mask = np.zeros(len(pts_2d), dtype=bool)
    inlier_mask[inliers.flatten()] = True

    print(f"[ACE] ✅ 定位成功: {len(inliers)}/{len(pts_2d)} 内点")

    return True, rvec, tvec, inlier_mask


# ── 主入口 ──
if __name__ == "__main__":
    model = train_ace_model()

    # 测试推理
    if model is not None:
        test_tiles = json.load(open("projections/tile_index.json"))
        if test_tiles:
            test_img = cv2.imread(test_tiles[0]["image_path"])
            if test_img is not None:
                # 相机内参（与渲染时一致）
                fov_deg = 75
                f = max(test_img.shape[1], test_img.shape[0]) / (2 * np.tan(np.deg2rad(fov_deg / 2)))
                K = np.array([[f, 0, test_img.shape[1]/2],
                              [0, f, test_img.shape[0]/2],
                              [0, 0, 1]])

                success, rvec, tvec, _ = ace_localize(model, test_img, K)
                print(f"[ACE] 测试定位: {'✅ 成功' if success else '❌ 失败'}")
