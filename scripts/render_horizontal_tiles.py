#!/usr/bin/env python3
"""
生成水平实验视角 tile（pitch=0°, 8 方向, fov=90；不得覆盖生产索引）

用途：仅用于对照实验。生产契约是 pitch=-15° 的四向斜地面视图；
水平八向不代表生产相机语义，也不得用于替代生产 MapTile。

用法：
    python scripts/render_horizontal_tiles.py           # 生成水平 tile
    python scripts/render_horizontal_tiles.py --max-poses 20  # 仅前 20 个位姿（测试）
    python scripts/render_horizontal_tiles.py --force    # 强制重建（含八叉树）
"""

import argparse
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    parser = argparse.ArgumentParser(description="生成水平视角 tile")
    parser.add_argument("--max-poses", type=int, default=None, help="限制位姿数（None=全部）")
    parser.add_argument("--force", action="store_true", help="强制重建")
    parser.add_argument("--las", default=None, help="LAS 文件路径（默认自动查找）")
    parser.add_argument("--output-dir", default="projections/experiments/horizontal")
    args = parser.parse_args()

    if os.path.abspath(args.output_dir) == os.path.abspath("projections"):
        parser.error("水平八向属于实验资产，--output-dir 不能指向生产 projections")

    from glob import glob as gg
    from services.las_processor.projection_octree import (
        project_las_multi_view_octree,
        HORIZONTAL_VIEW_DIRECTIONS,
        HORIZONTAL_FOV_DEG,
    )

    # 找 LAS
    las_path = args.las
    if not las_path:
        candidates = gg("las/*.las")
        if not candidates:
            print("ERROR: las/*.las not found")
            sys.exit(1)
        las_path = candidates[0]
        print(f"使用 LAS: {las_path}")

    print(f"{'='*60}")
    print(f"生成水平视角 tile")
    print(f"  pitch = 0° (水平)")
    print(f"  directions = {len(HORIZONTAL_VIEW_DIRECTIONS)} (45° 间隔)")
    print(f"  fov = {HORIZONTAL_FOV_DEG}°")
    print(f"  max_poses = {args.max_poses or 'all'}")
    print(f"  force = {args.force}")
    print(f"{'='*60}")

    def progress(msg, pct):
        print(f"[{pct:3d}%] {msg}")

    t0 = time.time()
    tiles = project_las_multi_view_octree(
        las_path=las_path,
        output_dir=args.output_dir,
        max_poses=args.max_poses,
        force_rebuild=args.force,
        view_directions=HORIZONTAL_VIEW_DIRECTIONS,
        fov_deg=HORIZONTAL_FOV_DEG,
        progress_callback=progress,
    )
    elapsed = time.time() - t0

    accepted = [t for t in tiles if t.get("accepted")]
    print(f"\n{'='*60}")
    print(f"完成: {elapsed:.1f}s")
    print(f"  总 tile: {len(tiles)}")
    print(f"  有效 tile: {len(accepted)}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
