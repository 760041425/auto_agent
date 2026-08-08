import subprocess
import sys
from pathlib import Path

from services.las_processor.tile_index_migration import build_ground_tile_publication


def test_production_renderer_rejects_partial_trajectory_limit():
    """TL-002-14: --max-poses 不得再次把不完整轨迹计划发布到生产目录。"""
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "scripts/render_ground_tiles.py", "--max-poses", "3"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "完整轨迹点和网格点" in result.stderr


def test_legacy_multi_pitch_index_is_filtered_to_four_ground_views():
    views = []
    legacy_tiles = []
    pose_id = 0
    for pitch in (-30.0, -15.0, 0.0, 15.0):
        for yaw in (0.0, 45.0, 90.0, 135.0, 180.0, 225.0, 270.0, 315.0):
            view = {
                "view_dir": f"yaw{int(yaw)}",
                "heading_deg": yaw,
                "yaw_deg": yaw,
                "pitch_deg": pitch,
                "roll_deg": 0.0,
                "x": 1.0,
                "y": 2.0,
                "z": 0.5,
                "qw": 1.0,
                "qx": 0.0,
                "qy": 0.0,
                "qz": 0.0,
            }
            views.append(view)
            image_path = f"projections/tiles/view_yaw{int(yaw)}_1.0_2.0_0.5_{pose_id}_p{int(pitch):+d}.png"
            legacy_tiles.append({
                "image_path": image_path,
                "npy_path": image_path.replace(".png", ".npy"),
                "normal_path": image_path.replace(".png", "_normal.npy"),
                "accepted": True,
                "pixel_count": 100,
            })
            pose_id += 1

    tiles, payload, stats = build_ground_tile_publication(
        legacy_tiles,
        {"views": views, "sample_interval_m": 5.0, "grid_interval_m": 10.0},
        fov_deg=75.0,
        path_exists=lambda _: True,
    )

    assert len(tiles) == 4
    assert {(tile["yaw_deg"], tile["pitch_deg"], tile["roll_deg"]) for tile in tiles} == {
        (0.0, -15.0, 0.0),
        (90.0, -15.0, 0.0),
        (180.0, -15.0, 0.0),
        (270.0, -15.0, 0.0),
    }
    assert all(tile["accepted"] for tile in tiles)
    assert all("camera_pose" in tile for tile in tiles)
    assert payload["view_contract"]["name"] == "four_direction_ground_facing_euler"
    assert payload["count"] == 1
    assert stats == {"planned": 4, "accepted": 4, "rejected": 0}


def test_missing_ground_asset_keeps_pose_and_rejection_reason():
    view = {
        "view_dir": "yaw0",
        "heading_deg": 0.0,
        "yaw_deg": 0.0,
        "pitch_deg": -15.0,
        "roll_deg": 0.0,
        "x": 1.0,
        "y": 2.0,
        "z": 0.5,
        "qw": 1.0,
        "qx": 0.0,
        "qy": 0.0,
        "qz": 0.0,
    }

    tiles, _, stats = build_ground_tile_publication(
        [],
        {"views": [view]},
        path_exists=lambda _: False,
    )

    assert tiles[0]["accepted"] is False
    assert tiles[0]["reject_reason"] == "legacy_asset_not_available"
    assert tiles[0]["camera_pose"]["euler_deg"]["pitch"] == -15.0
    assert stats["rejected"] == 1
