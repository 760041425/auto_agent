"""
推理期真实法线估计 — DSINE/MiDaS 封装（AC-008-01/02）

本批次（P0+P1，TL-008-01/02/03）真实模型加载留桩：禁止下载权重、
禁止 torch.hub.load、禁止新增依赖。`_load_model` 直接抛
``NormalModelNotReadyError`` 走常量 0.5 回退；单元测试均注入假
``_raw_infer`` 输出。

法线映射与训练对齐（``ace_trainer.SceneCoordinateDataset``:106 的
``normal=(normal+1)*0.5``）：``estimate_normal`` 统一返回 [0,1] float32。
接入真实权重时改 ``_load_model`` / ``_raw_infer``（见模块尾注释）。
"""

import logging

import numpy as np

logger = logging.getLogger("services.localizer.normal_estimator")

#: 法线来源标签（可选值）
NORMAL_SOURCE_DSINE = "dsine"
NORMAL_SOURCE_MIDAS = "mi_das"
NORMAL_SOURCE_FALLBACK = "constant_fallback"

#: 当前后端：D-008-01 待抽样试跑定案；此处先固定 dsine
_BACKEND = "dsine"

#: 回退常量：与训练真法线 (n+1)*0.5 映射后的分布均值中心对齐（007 约定）
FALLBACK_NORMAL_VALUE = 0.5

_last_source = NORMAL_SOURCE_FALLBACK


class NormalModelNotReadyError(RuntimeError):
    """真实法线模型未接入（本批次留桩）→ 上游应回退常量 0.5。"""


def normal_source_from_estimate() -> str:
    """最近一次 ``estimate_normal`` 的法线来源标签。

    可选值："dsine" / "mi_das" / "constant_fallback"。
    """
    return _last_source


def _normal_source_for_backend() -> str:
    return NORMAL_SOURCE_DSINE if _BACKEND == "dsine" else NORMAL_SOURCE_MIDAS


def _load_model():
    """懒加载 DSINE/MiDaS 权重模型（真实实现留桩）。

    本批次禁止权重下载 / torch.hub.load：直接抛
    ``NormalModelNotReadyError`` 使推理走回退分支。
    接真实权重时在此实现权重路径校验 + 懒加载缓存。
    """
    raise NormalModelNotReadyError(
        "真实法线模型未接入（本批次 TL-008-01/02/03 全部 mock）"
    )


def _raw_infer(image: np.ndarray) -> np.ndarray:
    """真实模型推理 → 原始法线输出（DSINE 通常 [-1,1]）。"""
    model = _load_model()
    return model(image)


def _to_unit_range(raw: np.ndarray) -> np.ndarray:
    """法线映射到 [0,1]：与训练对齐 (n+1)*0.5；已在 [0,1] 的保持原样。"""
    arr = np.asarray(raw, dtype=np.float32)
    if arr.size == 0:
        return arr
    if float(arr.min()) < 0.0 or float(arr.max()) > 1.0:
        return (arr + 1.0) * 0.5
    return arr


def estimate_normal(image: np.ndarray) -> np.ndarray:
    """估计 BGR uint8 (H,W,3) 查询图的法线 → (H,W,3) float32，值域 [0,1]。

    模型加载/推理失败时优雅回退常量 0.5（`normal_source="constant_fallback"`），
    不抛异常（RISK-008-01）。
    """
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