"""
推理期真实法线估计 — MiDaS (深度→法线) 封装（AC-008-01/02，P2 选型定案）。

P2 选型结论（D-008-01，2026-08-12）：
- **定案 MiDaS**（torch.hub intel-isl/MiDaS，MiDaS_small）：端到端可达，
  首次下载缓存后 ~5s 加载，单图 ~0.6s（CPU，远低于 RISK-008-02 的 10s 阈值），
  零新依赖（torch / torchvision / kornia 均已安装）；法线分布 [0,1] mean ≈ 0.45-0.49，
  与训练真法线 (n+1)*0.5 映射后的均值 0.5 对齐。
- **DSINE 备选暂不可达**（baegwangbin/DSINE，CVPR 2024 Oral）：预训练权重
  ``dsine_eval.zip`` 达 21GB，托管 Google Drive 且需浏览器式病毒扫描确认流程，
  非 pip 包（无 setup.py）。保留 ``NORMAL_SOURCE_DSINE`` 标签作未来选项。

环境依赖（torch.hub 首次下载需要外网）：
- macOS venv 的 certifi 可能过旧导致 SSL 验证失败；本模块在无明显式配置时
  自动回退到系统证书链 ``/etc/ssl/cert.pem``（始终可达，见 P2 探测）。
- torch.hub 首次加载会信任并缓存 ``intel-isl/MiDaS`` 与
  ``rwightman/gen-efficientnet-pytorch``（``trust_repo=True`` 自动写入
  ``TORCH_HOME/hub/trusted_list``）。

法线映射与训练对齐（``ace_trainer.SceneCoordinateDataset`` 的
``(normal+1)*0.5``）：``estimate_normal`` 统一返回 [0,1] float32。
"""

import logging
import os

import kornia
import numpy as np
import torch

logger = logging.getLogger("services.localizer.normal_estimator")

#: 法线来源标签（可选值）
NORMAL_SOURCE_DSINE = "dsine"          # 保留：DSINE 当前不可达，作未来选项
NORMAL_SOURCE_MIDAS = "mi_das"
NORMAL_SOURCE_FALLBACK = "constant_fallback"

#: 当前后端（P2 定案 MiDaS）。可通过环境变量 ``NORMAL_BACKEND`` 切换。
_BACKEND = os.environ.get("NORMAL_BACKEND", "midas")

#: 回退常量：与训练真法线 (n+1)*0.5 映射后的分布均值中心对齐（007 约定）
FALLBACK_NORMAL_VALUE = 0.5

#: 假设查询图视场角（度），用于 ``depth_to_normals`` 的相机矩阵焦距推算。
#: 可通过环境变量 ``NORMAL_FOV_DEG`` 覆盖。
FOV_DEG = float(os.environ.get("NORMAL_FOV_DEG", "75.0"))

# ---------------------------------------------------------------------------
# macOS venv certifi 过旧修复：在无明显式配置时回退到系统证书链（不覆盖用户）。
# 证据（P2 探测）：venv certifi 对 github/pytorch.org/huggingface 均 SSL 失败；
# /etc/ssl/cert.pem 全部可达。
# ---------------------------------------------------------------------------
if not os.environ.get("SSL_CERT_FILE") and not os.environ.get("REQUESTS_CA_BUNDLE"):
    _system_cert = "/etc/ssl/cert.pem"
    if os.path.exists(_system_cert):
        os.environ["SSL_CERT_FILE"] = _system_cert
        logger.debug("SSL_CERT_FILE 回退到系统证书链 %s", _system_cert)

_last_source = NORMAL_SOURCE_FALLBACK

#: 懒加载缓存（模块级单例）
_model = None
_transform = None


class NormalModelNotReadyError(RuntimeError):
    """真实法线模型未就绪（权重缺失 / 加载失败 / 后端不可达）→ 上游应回退常量 0.5。"""


def normal_source_from_estimate() -> str:
    """最近一次 ``estimate_normal`` 的法线来源标签。

    可选值："dsine" / "mi_das" / "constant_fallback"。
    """
    return _last_source


def _normal_source_for_backend() -> str:
    return NORMAL_SOURCE_DSINE if _BACKEND == "dsine" else NORMAL_SOURCE_MIDAS


def _load_model():
    """懒加载 MiDaS 模型与变换（模块级缓存）。

    失败统一抛 ``NormalModelNotReadyError``，由 ``estimate_normal`` 捕获并回退。
    """
    global _model, _transform
    if _model is not None and _transform is not None:
        return _model, _transform

    if _BACKEND == "dsine":
        # DSINE 当前不可达：21GB 权重 + Google Drive 确认墙 + 非 pip 包（P2 选型记录）
        raise NormalModelNotReadyError(
            "DSINE 未就绪：dsine_eval.zip (21GB) 托管 Google Drive 需确认流程，且非 pip 包"
        )

    try:
        midas = torch.hub.load(
            "intel-isl/MiDaS", "MiDaS_small", pretrained=True, trust_repo=True
        )
        midas.eval()
        midas_transforms = torch.hub.load("intel-isl/MiDaS", "transforms")
        _transform = midas_transforms.small_transform
        _model = midas
        logger.info(
            "MiDaS_small 加载完成（params %dk，backend=%s）",
            sum(p.numel() for p in _model.parameters()) // 1000,
            _BACKEND,
        )
        return _model, _transform
    except NormalModelNotReadyError:
        raise
    except Exception as exc:  # noqa: BLE001 — 统一包装为未就绪，由调用方回退
        raise NormalModelNotReadyError(f"MiDaS 加载失败: {exc}") from exc


def _raw_infer(image: np.ndarray) -> np.ndarray:
    """MiDaS 推理 + 深度→法线 → (H,W,3) float32，值域约 [-1,1]。

    输入为 BGR uint8 ``(H,W,3)``（OpenCV 默认顺序）。输出为近似单位法线，
    ``estimate_normal`` 会经 ``_to_unit_range`` 映射到 [0,1]。
    """
    model, transform = _load_model()
    h, w = image.shape[:2]

    # MiDaS 变换需要 HWC、通道顺序与训练一致（ImageNet 统计量按 RGB）。
    image_rgb = image[..., ::-1]
    inp = transform(image_rgb).to(next(model.parameters()).device)

    with torch.no_grad():
        pred = model(inp)  # (1, h', w')
        pred = torch.nn.functional.interpolate(
            pred.unsqueeze(1), size=(h, w), mode="bicubic", align_corners=False
        ).squeeze()  # (H, W)

    # 深度→法线：假设针孔相机，焦距由 FOV 推算；法线为局部朝向，对深度绝对尺度不敏感。
    d = pred.unsqueeze(0).unsqueeze(0)  # (1, 1, H, W)
    focal = w / (2.0 * np.tan(np.deg2rad(FOV_DEG) / 2.0))
    device = d.device
    K = torch.tensor(
        [[focal, 0.0, w / 2.0], [0.0, focal, h / 2.0], [0.0, 0.0, 1.0]],
        dtype=torch.float32,
        device=device,
    ).unsqueeze(0)  # (1, 3, 3)

    normals = kornia.geometry.depth_to_normals(d, K)  # (1, 3, H, W)
    return normals.squeeze(0).permute(1, 2, 0).cpu().numpy()  # (H, W, 3)


def _to_unit_range(raw: np.ndarray) -> np.ndarray:
    """法线映射到 [0,1]：与训练对齐 (n+1)*0.5；已在 [0,1] 的保持原样。"""
    arr = np.asarray(raw, dtype=np.float32)
    if arr.size == 0:
        return arr
    if float(arr.min()) < 0.0 or float(arr.max()) > 1.0:
        return (arr + 1.0) * 0.5
    return arr


def _validate_normal_input(image: np.ndarray) -> None:
    """校验 ``estimate_normal`` 输入，非法时抛 ValueError。

    这是编程错误（调用方传错dtype/维度），不是运行时模型失败，
    因此 ``estimate_normal`` 在 ``try`` 之前调用它，避免被末端
    ``except Exception`` 静默降级为常量 0.5（TL-008-07）。
    """
    if not isinstance(image, np.ndarray):
        raise ValueError(
            f"estimate_normal 输入应为 numpy.ndarray，实际 {type(image).__name__}"
        )
    if image.ndim != 3:
        raise ValueError(
            f"estimate_normal 输入应为 3 维 (H,W,3)，实际 {image.ndim} 维，shape={image.shape}"
        )
    if image.shape[2] != 3:
        raise ValueError(
            f"estimate_normal 输入应为 3 通道 (H,W,3)，实际 {image.shape[2]} 通道，shape={image.shape}"
        )
    if image.dtype != np.uint8:
        raise ValueError(
            f"estimate_normal 输入 dtype 应为 uint8，实际 {image.dtype}"
        )


def estimate_normal(image: np.ndarray) -> np.ndarray:
    """估计 BGR uint8 (H,W,3) 查询图的法线 → (H,W,3) float32，值域 [0,1]。

    输入校验失败（非 ndarray / 非 3 维 / 非 3 通道 / 非 uint8）显式抛
    ValueError，不被降级吞掉（TL-008-07）；模型加载/推理失败时优雅回退
    常量 0.5（``normal_source="constant_fallback"``），不抛异常（RISK-008-01）。
    """
    _validate_normal_input(image)
    global _last_source
    h, w = image.shape[:2]
    try:
        raw = _raw_infer(image)
        mapped = _to_unit_range(raw).astype(np.float32)
        if mapped.shape != (h, w, 3):
            raise ValueError(f"法线尺寸 {mapped.shape} 与输入 {(h, w, 3)} 不一致")
        _last_source = _normal_source_for_backend()
        return mapped
    except Exception as exc:  # noqa: BLE001 降级不允许崩溃
        logger.warning("法线估计失败，回退常量 0.5（fallback）: %s", exc)
        _last_source = NORMAL_SOURCE_FALLBACK
        return np.full((h, w, 3), FALLBACK_NORMAL_VALUE, dtype=np.float32)
