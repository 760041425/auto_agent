import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from services.las_processor import projection_octree as projection_octree_module
from services.las_processor.colmap_reader import read_images_txt, read_points3d_txt
from services.las_processor.projection import project_las_multi_view, _render_camera_like_points
from services.las_processor.projection_octree import build_projection_view_poses, _filter_trajectory_poses, prepare_octree_render_plan


@pytest.mark.integration
def test_read_images_txt():
    """TL-001-08: 读取确定性的 COLMAP 影像元数据。"""
    imgs = read_images_txt("las/images.txt")
    assert len(imgs) > 0
    assert imgs[0].image_id > 0
    assert len(imgs[0].points2d) > 0
    assert imgs[0].name


@pytest.mark.integration
def test_read_points3d_txt():
    """TL-001-08: 读取确定性的 COLMAP 三维点元数据。"""
    pts = read_points3d_txt("las/points3D.txt")
    assert len(pts) > 0
    first = list(pts.values())[0]
    assert first.x != 0


def test_camera_like_projection_renders_nontrivial_image():
    points = np.array([
        [0.0, 0.0, 3.0],
        [0.2, -0.1, 4.5],
        [-0.2, 0.1, 5.0],
    ], dtype=np.float32)
    colors = np.array([
        [255, 0, 0],
        [0, 255, 0],
        [0, 0, 255],
    ], dtype=np.uint8)
    camera_matrix = np.array([[180.0, 0.0, 32.0], [0.0, 180.0, 32.0], [0.0, 0.0, 1.0]], dtype=np.float64)

    img = _render_camera_like_points(points, colors, camera_matrix, 64, 64, radius=1.2)

    assert img.shape == (64, 64, 3)
    assert img.max() > 0
    assert img.std() > 0


def test_build_projection_view_poses_uses_euler_views(tmp_path):
    poses = [
        {"x": 0.0, "y": 0.0, "z": 1.0, "qx": 0.0, "qy": 0.0, "qz": 0.0, "qw": 1.0, "name": "p0"},
        {"x": 12.0, "y": 0.0, "z": 1.0, "qx": 0.0, "qy": 0.0, "qz": 0.0, "qw": 1.0, "name": "p1"},
    ]

    pose_file = build_projection_view_poses(poses, output_dir=str(tmp_path), sample_interval_m=10.0, use_grid_sampling=False)

    assert pose_file.exists()
    with open(pose_file) as f:
        payload = json.load(f)

    assert payload["sample_interval_m"] == 10.0
    assert len(payload["views"]) == 8  # 2 poses × 4 Euler directions
    assert all(view["yaw_deg"] in {0.0, 90.0, 180.0, 270.0} for view in payload["views"])
    assert all(view["pitch_deg"] == -15.0 for view in payload["views"])
    assert all(view["roll_deg"] == 0.0 for view in payload["views"])


def test_prepare_octree_render_plan_writes_trajectory_views(tmp_path):
    poses = [
        {"x": 0.0, "y": 0.0, "z": 1.0, "qx": 0.0, "qy": 0.0, "qz": 0.0, "qw": 1.0, "name": "p0"},
        {"x": 12.0, "y": 0.0, "z": 1.0, "qx": 0.0, "qy": 0.0, "qz": 0.0, "qw": 1.0, "name": "p1"},
    ]

    pose_file, views = prepare_octree_render_plan(
        poses,
        output_dir=str(tmp_path),
        sample_interval_m=10.0,
        use_grid_sampling=False,
    )

    assert pose_file.exists()
    assert len(views) == 8
    assert {view["x"] for view in views if view["view_dir"] == "yaw0"} == {0.0, 12.0}


def test_prepare_downsampled_las_uses_pdal(monkeypatch, tmp_path):
    source_las = tmp_path / "input.las"
    source_las.write_bytes(b"dummy")
    output_dir = tmp_path / "out"
    fake_pdal = tmp_path / "pdal"
    fake_pdal.write_text("#!/bin/sh\nexit 0\n")
    fake_pdal.chmod(0o755)

    calls = []

    def fake_run(cmd, capture_output, text, timeout):
        calls.append(cmd)
        Path(cmd[3]).write_bytes(b"ok")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(projection_octree_module.shutil, "which", lambda name: str(fake_pdal))
    monkeypatch.setattr(projection_octree_module.subprocess, "run", fake_run)

    sampled_path = projection_octree_module._prepare_downsampled_las(str(source_las), str(output_dir))

    assert Path(sampled_path).exists()
    assert calls[0][0] == str(fake_pdal)
    assert calls[0][4] == "filters.voxeldownsize"
    assert calls[0][5] == "--filters.voxeldownsize.cell=0.02"


def test_prepare_downsampled_las_uses_env_pdal_bin(monkeypatch, tmp_path):
    source_las = tmp_path / "input.las"
    source_las.write_bytes(b"dummy")
    output_dir = tmp_path / "out"
    fake_pdal = tmp_path / "pdal"
    fake_pdal.write_text("#!/bin/sh\nexit 0\n")
    fake_pdal.chmod(0o755)

    calls = []

    def fake_run(cmd, capture_output, text, timeout):
        calls.append(cmd)
        Path(cmd[3]).write_bytes(b"ok")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setenv("PDAL_BIN", str(fake_pdal))
    monkeypatch.setattr(projection_octree_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(projection_octree_module.subprocess, "run", fake_run)

    sampled_path = projection_octree_module._prepare_downsampled_las(str(source_las), str(output_dir))

    assert Path(sampled_path).exists()
    assert calls[0][0] == str(fake_pdal)


def test_filter_trajectory_poses_respects_time_and_distance():
    poses = [
        {"ts": 0.0, "x": 0.0, "y": 0.0, "z": 0.0},
        {"ts": 0.5, "x": 0.5, "y": 0.0, "z": 0.0},
        {"ts": 1.0, "x": 3.0, "y": 0.0, "z": 0.0},
        {"ts": 1.2, "x": 3.1, "y": 0.0, "z": 0.0},
    ]

    filtered = _filter_trajectory_poses(poses, min_time_sec=1.0, min_dist_m=4.0)

    assert [pose["ts"] for pose in filtered] == [0.0, 1.0]


@pytest.mark.system
def test_las_projection():
    las_files = [f for f in sorted(Path("las").glob("*.las")) if "subsample" not in f.name]
    las_path = str(las_files[0]) if las_files else "las/default_2026-05-28-112428.las"
    result = project_las_multi_view(
        las_path,
        "projections/test_proj",
        max_poses=1,
    )
    assert len(result) >= 8
    first = result[0]
    assert first["width"] > 0
    assert first["height"] > 0
    assert Path(first["image_path"]).exists()
    assert Path(first["coord_map_path"]).exists()

    with open(first["coord_map_path"]) as f:
        data = json.load(f)
    assert len(data["pixels"]) > 0

    import shutil
    shutil.rmtree("projections/test_proj", ignore_errors=True)


@pytest.mark.system
def test_api_health():
    import socket
    import requests
    import uvicorn
    import threading
    import time

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    def run():
        uvicorn.run("api.main:app", host="127.0.0.1", port=port, log_level="error")

    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(3)

    base = f"http://127.0.0.1:{port}"
    for _ in range(5):
        try:
            r = requests.get(f"{base}/api/health", timeout=2)
            if r.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(1)

    r = requests.get(f"{base}/api/health")
    assert r.status_code == 200

    r = requests.get(f"{base}/api/images")
    assert r.status_code == 200

    r = requests.get(f"{base}/")
    assert r.status_code == 200
