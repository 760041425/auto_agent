"""010 实验适配器：MoGe-2 ViT-S 法线输出，不接入生产定位路由。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np


MODEL_ID = "Ruicheng/moge-2-vits-normal"
MODEL_REVISION = "679230677b4d282c6f304189a93e98e14f085902"
MODEL_SOURCE = "https://huggingface.co/Ruicheng/moge-2-vits-normal"
CODE_REVISION = "microsoft/MoGe@42acd8f"
NORMAL_SOURCE = "moge_2_vits_normal"
NORMAL_COORDINATE_FRAME = "opencv_camera_x_right_y_down_z_forward"


def load_moge_normal_model(
    *, cache_dir: str, device: str = "cpu"
) -> tuple[Any, dict[str, Any]]:
    """从官方权重缓存加载固定 MoGe-2 模型，并返回可追踪元数据。"""
    from huggingface_hub import hf_hub_download
    from moge.model.v2 import MoGeModel

    checkpoint_path = hf_hub_download(
        repo_id=MODEL_ID,
        filename="model.pt",
        revision=MODEL_REVISION,
        cache_dir=cache_dir,
    )
    model = MoGeModel.from_pretrained(checkpoint_path).to(device).eval()
    checkpoint = Path(checkpoint_path)
    return model, {
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "model_source": MODEL_SOURCE,
        "code_revision": CODE_REVISION,
        "checkpoint_path": str(checkpoint),
        "checkpoint_bytes": checkpoint.stat().st_size,
        "device": device,
    }


def predict_moge_normals(
    model: Any,
    image_rgb: np.ndarray,
    *,
    device: str = "cpu",
    num_tokens: int = 1200,
) -> dict[str, Any]:
    """把 RGB uint8 图像转换为带来源和坐标系的内部法线契约。"""
    import torch

    if image_rgb.ndim != 3 or image_rgb.shape[2] != 3:
        raise ValueError("image_rgb 必须是 H×W×3")
    image = torch.from_numpy(np.ascontiguousarray(image_rgb))
    image = image.permute(2, 0, 1).float().div_(255.0).to(device)
    output = model.infer(
        image,
        num_tokens=num_tokens,
        apply_mask=True,
        use_fp16=device != "cpu",
    )
    if "normal" not in output:
        raise RuntimeError("MoGe-2 权重没有返回 normal")

    normal = output["normal"].detach().float().cpu().numpy().astype(np.float32)
    lengths = np.linalg.norm(normal, axis=-1)
    valid_mask = np.isfinite(normal).all(axis=-1) & (lengths > 0.5)
    return {
        "normal": normal,
        "valid_mask": valid_mask,
        "normal_source": NORMAL_SOURCE,
        "coordinate_frame": NORMAL_COORDINATE_FRAME,
        "num_tokens": num_tokens,
    }
