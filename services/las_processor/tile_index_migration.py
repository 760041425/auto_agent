"""Recover a production four-direction ground-facing MapTile publication."""

from __future__ import annotations

import os
import re
from collections.abc import Callable

from services.las_processor.projection_octree import (
    PITCH_DEG,
    _build_colmap_line,
    _build_tile_index_record,
)

_POSE_ID_PATTERN = re.compile(r"_(\d+)_p[+-]?\d+\.png$")
_PRODUCTION_YAWS = {0.0, 90.0, 180.0, 270.0}


def _legacy_pose_id(tile: dict) -> int | None:
    match = _POSE_ID_PATTERN.search(str(tile.get("image_path", "")))
    return int(match.group(1)) if match else None


def _is_ground_view(view: dict) -> bool:
    yaw = float(view.get("yaw_deg", view.get("heading_deg", 0.0)))
    pitch = float(view.get("pitch_deg", PITCH_DEG))
    roll = float(view.get("roll_deg", 0.0))
    return yaw in _PRODUCTION_YAWS and pitch == PITCH_DEG and roll == 0.0


def build_ground_tile_publication(
    legacy_tiles: list[dict],
    pose_payload: dict,
    *,
    fov_deg: float = 75.0,
    z_bias: float = 0.0,
    offset_xyz: tuple[float, float, float] = (0.0, 0.0, 0.0),
    path_exists: Callable[[str], bool] = os.path.isfile,
) -> tuple[list[dict], dict, dict[str, int]]:
    """Filter a legacy multi-view publication to the production view contract."""
    legacy_by_pose_id = {
        pose_id: tile
        for tile in legacy_tiles
        if (pose_id := _legacy_pose_id(tile)) is not None
    }
    ground_tiles = []
    ground_views = []

    for pose_id, view in enumerate(pose_payload.get("views", [])):
        if not _is_ground_view(view):
            continue
        ground_views.append(dict(view))
        pose = dict(view)
        render_line = _build_colmap_line(
            pose,
            offset_xyz,
            z_bias,
            float(view.get("yaw_deg", view.get("heading_deg", 0.0))),
            float(view.get("pitch_deg", PITCH_DEG)),
            float(view.get("roll_deg", 0.0)),
        )
        legacy = legacy_by_pose_id.get(pose_id, {})
        paths = [
            str(legacy.get("image_path", "")),
            str(legacy.get("npy_path", "")),
            str(legacy.get("normal_path", "")),
        ]
        accepted = bool(legacy.get("accepted")) and all(path and path_exists(path) for path in paths)
        ground_tiles.append(_build_tile_index_record(
            view=view,
            pose_id=pose_id,
            render_line=render_line,
            image_path=paths[0] if accepted else "",
            npy_path=paths[1] if accepted else "",
            normal_path=paths[2] if accepted else "",
            width=int(legacy.get("width", 512)),
            height=int(legacy.get("height", 512)),
            fov_deg=fov_deg,
            pixel_count=int(legacy.get("pixel_count", 0)) if accepted else 0,
            accepted=accepted,
            reject_reason=None if accepted else "legacy_asset_not_available",
            reused=accepted,
        ))

    position_count = len({
        (float(view["x"]), float(view["y"]), float(view["z"]))
        for view in ground_views
    })
    publication_payload = {
        "schema_version": 2,
        "view_contract": {
            "name": "four_direction_ground_facing_euler",
            "yaw_deg": sorted(_PRODUCTION_YAWS),
            "pitch_deg": PITCH_DEG,
            "roll_deg": 0.0,
            "pitch_semantics": "negative_world_z_is_ground_facing",
        },
        "sample_interval_m": float(pose_payload.get("sample_interval_m", 5.0)),
        "grid_interval_m": float(pose_payload.get("grid_interval_m", 10.0)),
        "use_grid_sampling": bool(pose_payload.get("use_grid_sampling", True)),
        "count": position_count,
        "views": ground_views,
    }
    accepted_count = sum(1 for tile in ground_tiles if tile["accepted"])
    stats = {
        "planned": len(ground_tiles),
        "accepted": accepted_count,
        "rejected": len(ground_tiles) - accepted_count,
    }
    return ground_tiles, publication_payload, stats
