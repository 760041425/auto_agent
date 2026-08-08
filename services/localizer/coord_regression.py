"""
坐标回归网络 — 补全缺失的 CoordRegression / CoordRegressionFCN 符号

原来 ``api/routes/localize.py:390`` 引用 ``CoordRegression(in_channels=6)``，
``salad_roma.py:1030`` 引用 ``CoordRegressionFCN(in_channels=6)``，
但这两个类在 ``ace_trainer.py`` 里都未定义（只有 ``ACERegressor``），
导致 ACE 端点存在 latent ImportError。

本模块提供与 ``ACERegressor`` 对齐的 FCN 版本，支持 3ch / 6ch 输入，
输出 1/8 subsampled 场景坐标，用于 PnP 定位。
"""

import logging
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import torch
import torch.nn as nn

_ace_dir = Path(__file__).parent / "ace"
import sys as _sys
if str(_ace_dir.parent) not in _sys.path:
    _sys.path.insert(0, str(_ace_dir.parent))

from ace.ace_network import Encoder, Head  # noqa: E402

_logger = logging.getLogger("localizer.coord_regression")

DEVICE = torch.device(
    "cuda" if torch.cuda.is_available() else
    "mps" if torch.backends.mps.is_available() else "cpu"
)


class _EncoderNCh(Encoder):
    """泛化 Encoder：第一层支持任意输入通道数。"""

    def __init__(self, in_channels: int = 6, out_channels: int = 256):
        super().__init__(out_channels=out_channels)
        self.conv1 = nn.Conv2d(in_channels, 32, 3, 1, 1)


class CoordRegressionFCN(nn.Module):
    """FCN 场景坐标回归。

    输入：(B, in_channels, H, W) — 通常 6ch = RGB(3) + Normal(3)
    输出：(B, 3, H/8, W/8) — 每个像素的 3D 场景坐标
    """

    OUTPUT_SUBSAMPLE = 8

    def __init__(
        self,
        in_channels: int = 6,
        mean: Optional[torch.Tensor] = None,
        num_head_blocks: int = 1,
        use_homogeneous: bool = False,
        num_encoder_features: int = 256,
    ):
        super().__init__()
        self.feature_dim = num_encoder_features
        self.encoder = _EncoderNCh(in_channels=in_channels, out_channels=num_encoder_features)
        if mean is None:
            mean = torch.zeros(3)
        self.heads = Head(
            mean, num_head_blocks, use_homogeneous,
            in_channels=num_encoder_features,
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        features = self.encoder(x)
        return self.heads(features)


# 兼容别名，对齐 api/routes/localize.py:390 的使用方式
CoordRegression = CoordRegressionFCN


def _detect_architecture(state_dict: dict) -> dict:
    """从 state_dict 推断架构参数。"""
    num_head_blocks = sum(
        1 for k in state_dict if k.startswith("heads.") and k.endswith("c0.weight")
    )
    use_homogeneous = state_dict.get("heads.fc3.weight", torch.empty(0)).shape[0] == 4
    num_encoder_features = state_dict.get(
        "encoder.res2_conv3.weight", torch.empty(0)
    ).shape[0]
    in_ch = state_dict.get("encoder.conv1.weight", torch.empty(0)).shape[1]
    return {
        "num_head_blocks": max(num_head_blocks, 1),
        "use_homogeneous": use_homogeneous,
        "num_encoder_features": max(num_encoder_features, 256),
        "in_channels": max(in_ch, 1),
    }


def load_coord_regression(
    model_path: str = "projections/ace_model.pth",
    in_channels: int = 6,
    device: Optional[torch.device] = None,
) -> nn.Module:
    """加载 CoordRegression 模型，自动检测架构。

    若 state_dict 含 ``encoder.conv1.weight`` 则按其输入通道数创建；
    否则退回 ``in_channels`` 参数。
    """
    if device is None:
        device = DEVICE

    state_dict = torch.load(model_path, map_location="cpu", weights_only=False)
    cfg = _detect_architecture(state_dict)
    use_in = cfg.get("in_channels", in_channels)
    if use_in <= 0:
        use_in = in_channels

    model = CoordRegressionFCN(
        in_channels=use_in,
        num_head_blocks=cfg["num_head_blocks"],
        use_homogeneous=cfg["use_homogeneous"],
        num_encoder_features=cfg["num_encoder_features"],
    )
    try:
        model.load_state_dict(state_dict)
    except RuntimeError:
        _logger.warning(
            "strict load failed, trying with encoder conv1 skipped "
            "(in_channels mismatch %d vs requested %d)", use_in, in_channels
        )
        model = CoordRegressionFCN(
            in_channels=in_channels,
            num_head_blocks=cfg["num_head_blocks"],
            use_homogeneous=cfg["use_homogeneous"],
            num_encoder_features=cfg["num_encoder_features"],
        )
        filtered = {k: v for k, v in state_dict.items()
                    if not k.startswith("encoder.conv1.")}
        missing, unexpected = model.load_state_dict(filtered, strict=False)
        _logger.info("loaded with missing=%s unexpected=%s",
                     missing[:5], unexpected[:5])
    model.to(device).eval()
    return model


@torch.no_grad()
def predict_dense(
    model: nn.Module,
    image: np.ndarray,
    normal_map: Optional[np.ndarray] = None,
    device: Optional[torch.device] = None,
    max_points: int = 2000,
):
    """全图推理，返回稀疏采样后的 2D/3D/置信度。

    与 ``ace_trainer.ace_predict_dense`` 行为一致，但支持 3ch/6ch 自适应。
    """
    if device is None:
        device = next(model.parameters()).device

    h, w = image.shape[:2]
    rgb = cv2.resize(image, (w, h)).astype(np.float32) / 255.0
    if normal_map is not None and model.encoder.conv1.in_channels >= 6:
        nm = cv2.resize(normal_map, (w, h)).astype(np.float32)
        if nm.max() > 1.5:
            nm = nm / 255.0
        inp = np.concatenate([rgb, nm], axis=-1)
    else:
        inp = rgb[:, :, :3]

    tensor = torch.from_numpy(inp.transpose(2, 0, 1)).unsqueeze(0).to(device)
    coords = model(tensor).squeeze(0).cpu().numpy()  # (3, h/8, w/8)

    sub = CoordRegressionFCN.OUTPUT_SUBSAMPLE
    ys = np.arange(0, coords.shape[1])
    xs = np.arange(0, coords.shape[2])
    gy, gx = np.meshgrid(ys, xs, indexing="ij")
    pts_2d = np.stack([gx.ravel() * sub + sub / 2.0,
                       gy.ravel() * sub + sub / 2.0], axis=-1)
    pts_3d = coords.reshape(3, -1).T

    if len(pts_2d) > max_points:
        idx = np.random.choice(len(pts_2d), max_points, replace=False)
        pts_2d = pts_2d[idx]
        pts_3d = pts_3d[idx]

    confidence = np.ones(len(pts_2d), dtype=np.float32)
    return pts_2d, pts_3d, confidence
