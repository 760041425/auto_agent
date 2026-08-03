#!/usr/bin/env python3
"""生成生产 MapTile：四向、pitch=-15°、roll=0° 的斜向地面投影。"""

import argparse
import os
import sys
from glob import glob

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> None:
    parser = argparse.ArgumentParser(description="生成四向斜地面生产 MapTile")
    parser.add_argument("--max-poses", type=int, default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--las", default=None)
    parser.add_argument("--output-dir", default="projections")
    args = parser.parse_args()

    if os.path.abspath(args.output_dir) == os.path.abspath("projections") and args.max_poses is not None:
        parser.error("生产 MapTile 必须包含完整轨迹点和网格点；--max-poses 只能用于隔离实验目录")

    from services.las_processor.projection_octree import (
        GROUND_VIEW_DIRECTIONS,
        project_las_multi_view_octree,
    )

    las_path = args.las
    if not las_path:
        candidates = glob("las/*.las")
        if not candidates:
            parser.error("las/*.las not found")
        las_path = candidates[0]

    print("生产投影契约: 完整轨迹点 + 网格点, yaw=0/90/180/270, pitch=-15, roll=0")
    tiles = project_las_multi_view_octree(
        las_path=las_path,
        output_dir=args.output_dir,
        max_poses=args.max_poses,
        force_rebuild=args.force,
        view_directions=GROUND_VIEW_DIRECTIONS,
        fov_deg=75.0,
        progress_callback=lambda message, percent: print(f"[{percent:3d}%] {message}"),
    )
    accepted = sum(1 for tile in tiles if tile.get("accepted"))
    print(f"完成: {len(tiles)} 个计划视角，{accepted} 个 accepted MapTile")


if __name__ == "__main__":
    main()
