import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from services.las_processor import projection_octree as projection_octree_module
from services.las_processor.colmap_reader import read_images_txt, read_points3d_txt
from services.las_processor.projection import project_las_multi_view, _render_camera_like_points
from services.las_processor.projection_octree import (
    _build_colmap_line,
    _filter_trajectory_poses,
    _quat_to_rotmat_colmap,
    _stagger_grid_poses,
    build_projection_view_poses,
    prepare_octree_render_plan,
)


@pytest.mark.integration
def test_read_images_txt():
    """TL-001-08: 读取确定性的 COLMAP 影像元数据。"""
    imgs = read_images_txt("las/images.txt")
    assert len(imgs) > 0
    assert imgs[0].image_id > 0
    assert len(imgs[0].points2d) > 0
    assert imgs[0].name


def _points3d_has_data(path: str = "las/points3D.txt") -> bool:
    """COLMAP points3D.txt 是否含真实点（非空占位）。"""
    try:
        with open(path) as f:
            for line in f:
                s = line.strip()
                if s and not s.startswith("#"):
                    return True
    except FileNotFoundError:
        pass
    return False


@pytest.mark.skipif(
    not _points3d_has_data(),
    reason="las/points3D.txt 为 0 点空占位；放入真实 COLMAP 重建产物后自动启用",
)
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
    assert payload["view_contract"]["name"] == "four_direction_ground_facing_euler"
    for view in payload["views"]:
        line = _build_colmap_line(view, (0.0, 0.0, 0.0))
        qw, qx, qy, qz = [float(value) for value in line.split()[:4]]
        rotation = _quat_to_rotmat_colmap(qw, qx, qy, qz)
        assert rotation[2, 2] < 0.0  # camera forward has negative world-Z: ground-facing


def test_render_plan_combines_all_trajectory_and_grid_positions(tmp_path):
    """TL-002-14: 生产计划必须是完整轨迹位置与网格位置的并集再乘四向。"""
    octree_dir = tmp_path / "octree_data"
    octree_dir.mkdir()
    with open(octree_dir / "manifest.json", "w") as stream:
        json.dump({"root_min": [0.0, 0.0, 0.0], "root_max": [40.0, 40.0, 10.0]}, stream)
    poses = [
        {"x": 1.0, "y": 1.0, "z": 1.0, "name": "trajectory_0"},
        {"x": 2.0, "y": 2.0, "z": 1.0, "name": "trajectory_1"},
    ]

    pose_file, views = prepare_octree_render_plan(
        poses,
        output_dir=str(tmp_path),
        grid_interval_m=10.0,
        use_grid_sampling=True,
    )
    with open(pose_file) as stream:
        payload = json.load(stream)

    # 点云边界扣除 15m margin 后只有 (15,15) 一个网格点：2 轨迹 + 1 网格。
    assert payload["count"] == 3
    assert len(views) == 3 * 4
    assert {view["name"] for view in views} == {"trajectory_0", "trajectory_1", "grid_15.0_15.0"}
    assert all(view["pitch_deg"] == -15.0 and view["roll_deg"] == 0.0 for view in views)


def test_grid_poses_stagger_away_from_trajectory_positions():
    """网格点与轨迹点错开：距轨迹点过近的网格点应偏移到轨迹点旁 5m 处。"""
    grid_poses = [
        {"x": 5.0, "y": 5.0, "z": 1.0, "name": "grid_5.0_5.0"},   # 距轨迹 0.1m → 触发偏移
        {"x": 20.0, "y": 20.0, "z": 1.0, "name": "grid_20.0_20.0"},  # 远离轨迹 → 保留
        {"x": 5.0, "y": 5.0, "z": 1.0, "name": "grid_5.0_5.0_dup"},  # 重复点 → 去重
    ]
    trajectory_poses = [
        {"x": 5.0, "y": 4.9, "z": 1.0, "name": "traj_0"},
    ]

    result = _stagger_grid_poses(grid_poses, trajectory_poses, offset_m=5.0)
    result_by_xy = {(p["x"], p["y"]) for p in result}

    # 20.0,20.0 远离轨迹点，保留原坐标
    assert (20.0, 20.0) in result_by_xy

    # 5.0,5.0（距轨迹 0.1m < 5m）应偏移到距轨迹恰好 5m
    # 方向 (5.0,5.0)→(5.0,4.9) 即 +Y，新坐标 = (5.0, 4.9+5.0) = (5.0, 9.9)
    staggered_xy = (5.0, 9.9)
    assert staggered_xy in result_by_xy
    dist = math.sqrt((staggered_xy[0] - 5.0) ** 2 + (staggered_xy[1] - 4.9) ** 2)
    assert abs(dist - 5.0) < 1e-6

    # 重复点去重：两个 (5.0,5.0) 错开到同一位置，只保留一个
    assert len(result) == 2


def test_grid_poses_offset_when_coincident_with_trajectory():
    """网格点恰好与轨迹点重合时，应向 +X 方向偏移 5m。"""
    grid_poses = [{"x": 3.0, "y": 3.0, "z": 1.0, "name": "grid_3.0_3.0"}]
    trajectory_poses = [{"x": 3.0, "y": 3.0, "z": 1.0, "name": "traj_0"}]

    result = _stagger_grid_poses(grid_poses, trajectory_poses, offset_m=5.0)
    assert len(result) == 1
    assert result[0]["x"] == 8.0  # 3.0 + 5.0
    assert result[0]["y"] == 3.0


def test_median_depth_detects_camera_inside_structure():
    """建筑内部检测：中位深度 < MIN_MEDIAN_DEPTH_M 应视为相机在建筑内。"""
    from services.las_processor.projection_octree import MIN_MEDIAN_DEPTH_M

    # 模拟深度图：大部分有效点很近（< 1m），相机在建筑内部
    depth_inside = np.array([
        [0.5, 0.6, 0.8, 0.0, 0.0],
        [0.7, 0.9, 1.0, 0.0, 0.0],
        [0.6, 0.5, 0.7, 0.0, 0.0],
    ], dtype=np.float32)
    valid = depth_inside[depth_inside > 0]
    median_inside = float(np.median(valid))
    assert median_inside < MIN_MEDIAN_DEPTH_M, f"内部中位深度 {median_inside} 应 < {MIN_MEDIAN_DEPTH_M}"

    # 模拟深度图：有效点延伸到远处，相机在室外
    depth_outside = np.array([
        [5.0, 8.0, 12.0, 0.0, 0.0],
        [6.0, 10.0, 15.0, 0.0, 0.0],
        [7.0, 9.0, 20.0, 0.0, 0.0],
    ], dtype=np.float32)
    valid = depth_outside[depth_outside > 0]
    median_outside = float(np.median(valid))
    assert median_outside >= MIN_MEDIAN_DEPTH_M, f"室外中位深度 {median_outside} 应 >= {MIN_MEDIAN_DEPTH_M}"


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


def test_explicit_zero_euler_does_not_fall_back_to_trajectory_quaternion():
    """TL-002-10: 显式 0/0/0 仍是 Euler 指令，不能使用轨迹四元数。"""
    pose = {
        "x": 1.0,
        "y": 2.0,
        "z": 3.0,
        "qw": np.sqrt(0.5),
        "qx": 0.0,
        "qy": 0.0,
        "qz": np.sqrt(0.5),
        "yaw_deg": 0.0,
        "pitch_deg": 0.0,
        "roll_deg": 0.0,
    }

    line = _build_colmap_line(pose, (0.0, 0.0, 0.0))
    qw, qx, qy, qz = [float(value) for value in line.split()[:4]]
    rotation = _quat_to_rotmat_colmap(qw, qx, qy, qz)

    assert rotation[2].tolist() == pytest.approx([0.0, 1.0, 0.0], abs=1e-6)


def test_map_tile_record_contains_actual_render_pose():
    """TL-002-11: MapTile 必须携带实际 Euler、四元数和 COLMAP 渲染位姿。"""
    from services.las_processor.projection_octree import _build_tile_index_record

    view = {
        "view_dir": "yaw90",
        "x": 1.0,
        "y": 2.0,
        "z": 0.5,
        "yaw_deg": 90.0,
        "pitch_deg": -15.0,
        "roll_deg": 0.0,
    }
    line = _build_colmap_line(view, (0.0, 0.0, 0.0))

    record = _build_tile_index_record(
        view=view,
        pose_id=7,
        render_line=line,
        image_path="projections/tiles/example.png",
        npy_path="projections/tiles/example.npy",
        normal_path="projections/tiles/example_normal.npy",
        width=512,
        height=512,
        fov_deg=75.0,
        pixel_count=100,
        accepted=True,
    )

    assert record["pose_id"] == 7
    assert record["camera_pose"]["position_local_m"] == {"x": 1.0, "y": 2.0, "z": 0.5}
    assert record["camera_pose"]["euler_deg"] == {"yaw": 90.0, "pitch": -15.0, "roll": 0.0}
    assert len(record["camera_pose"]["quaternion_wxyz"]) == 4
    assert record["camera_pose"]["colmap_line"] == line
    assert record["camera_pose"]["coordinate_frame"] == "slam_local"
    assert record["camera_pose"]["rotation_convention"] == "world_to_camera_wxyz"
    assert record["camera"]["fov_deg"] == 75.0

    rejected = _build_tile_index_record(
        view=view,
        pose_id=7,
        render_line=line,
        image_path="",
        npy_path="",
        normal_path="",
        width=512,
        height=512,
        fov_deg=75.0,
        pixel_count=0,
        accepted=False,
        reject_reason="black_pixel_threshold",
    )
    assert rejected["status"] == "rejected"
    assert rejected["reject_reason"] == "black_pixel_threshold"
    assert rejected["camera_pose"] == record["camera_pose"]


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
