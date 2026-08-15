#!/usr/bin/env python3
"""Safely republish an existing multi-view index as four ground-facing views."""

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _atomic_json_write(path: Path, value) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with open(temporary, "w") as stream:
        json.dump(value, stream, indent=2)
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="迁移为四向斜地面生产 MapTile 索引")
    parser.add_argument("--projection-dir", default="projections")
    parser.add_argument("--source-tile-index", default=None)
    parser.add_argument("--source-pose-file", default=None)
    parser.add_argument("--legacy-fov-deg", type=float, default=90.0)
    parser.add_argument("--apply", action="store_true", help="备份后写入；默认只预览")
    args = parser.parse_args()

    projection_dir = Path(args.projection_dir)
    tile_index_path = projection_dir / "tile_index.json"
    pose_path = projection_dir / "projection_view_poses.json"
    if not tile_index_path.is_file() or not pose_path.is_file():
        parser.error("tile_index.json or projection_view_poses.json is missing")

    from services.las_processor.projection_octree import _load_z_bias
    from services.las_processor.tile_index_migration import build_ground_tile_publication

    source_tile_index = Path(args.source_tile_index) if args.source_tile_index else tile_index_path
    source_pose_path = Path(args.source_pose_file) if args.source_pose_file else pose_path
    with open(source_tile_index) as stream:
        legacy_tiles = json.load(stream)
    with open(source_pose_path) as stream:
        pose_payload = json.load(stream)

    tiles, payload, stats = build_ground_tile_publication(
        legacy_tiles,
        pose_payload,
        fov_deg=args.legacy_fov_deg,
        z_bias=_load_z_bias("las"),
    )
    print(json.dumps(stats, ensure_ascii=False))
    if not args.apply:
        print("预览完成；添加 --apply 才会写入")
        return

    stamp = time.strftime("%Y%m%dT%H%M%S")
    tile_backup = tile_index_path.with_name(f"tile_index.multi-pitch-backup-{stamp}.json")
    pose_backup = pose_path.with_name(f"projection_view_poses.multi-pitch-backup-{stamp}.json")
    shutil.copy2(tile_index_path, tile_backup)
    shutil.copy2(pose_path, pose_backup)
    _atomic_json_write(tile_index_path, tiles)
    _atomic_json_write(pose_path, payload)
    print(f"已写入 {tile_index_path} 和 {pose_path}")
    print(f"备份: {tile_backup}, {pose_backup}")


if __name__ == "__main__":
    main()
