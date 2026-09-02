"""010 hloc 轻量官方基线契约。"""

import platform

import numpy as np
import torch

from services.localizer.hloc_baseline_010 import (
    load_hloc_frontend_classes,
    probe_hloc_dependencies,
    unpack_hloc_matches,
)


def test_hloc_dependency_probe_is_available_or_explicitly_skipped():
    """TL-010-09：缺依赖不得静默回退为现有匹配器。"""
    status = probe_hloc_dependencies()

    assert status["status"] in {"available", "skipped"}
    assert isinstance(status["versions"], dict)
    assert isinstance(status["missing"], list)
    if status["status"] == "available":
        assert not status["missing"]
        assert {"hloc", "pycolmap", "lightglue"} <= set(status["versions"])
    else:
        assert status["missing"]
        assert status["reason"] == "missing_official_hloc_dependencies"


def test_hloc_probe_blocks_unsafe_pycolmap_torch_coimport_on_macos():
    """TL-010-09：双 libomp 环境只能运行隔离前端，禁止不安全环境变量绕过。"""
    status = probe_hloc_dependencies()

    assert status["unsafe_workaround_used"] is False
    if status["status"] == "available" and platform.system() == "Darwin":
        assert status["frontend_status"] == "available"
        assert status["full_pipeline_status"] == "skipped"
        assert status["full_pipeline_reason"] == "duplicate_openmp_runtime"


def test_hloc_frontend_loader_avoids_eager_optional_extractors():
    """TL-010-09：仅加载基准所需 SuperPoint+LightGlue，避免第二份 libomp。"""
    superpoint, lightglue = load_hloc_frontend_classes()

    assert superpoint.__name__ == "SuperPoint"
    assert lightglue.__name__ == "LightGlue"
    assert probe_hloc_dependencies()["frontend_status"] == "available"


def test_hloc_match_output_maps_indices_to_query_and_tile_keypoints():
    """TL-010-09：官方前端输出必须稳定映射为项目的 query↔tile 点对。"""
    features0 = {"keypoints": torch.tensor([[[1.0, 2.0], [3.0, 4.0]]])}
    features1 = {"keypoints": torch.tensor([[[10.0, 20.0], [30.0, 40.0]]])}
    matches = {
        "matches": [torch.tensor([[1, 0], [0, 1]])],
        "scores": [torch.tensor([0.8, 0.9])],
    }

    query, tile, scores = unpack_hloc_matches(features0, features1, matches)

    assert np.array_equal(query, np.array([[3.0, 4.0], [1.0, 2.0]]))
    assert np.array_equal(tile, np.array([[10.0, 20.0], [30.0, 40.0]]))
    assert np.allclose(scores, np.array([0.8, 0.9]))
