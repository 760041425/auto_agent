#!/usr/bin/env python3
"""
多 pitch 实验视角 tile 渲染（不得覆盖生产索引）

pitch ∈ {-30°, -15°, 0°, +15°}
每 pitch 8 yaw = 32 向/pose
BLACK_PIXEL_THRESHOLD = 0.95（宽松过滤）

用法:
    python scripts/render_multi_pitch_tiles.py              # 全部位姿
    python scripts/render_multi_pitch_tiles.py --max-poses 10  # 仅前 10 个
    python scripts/render_multi_pitch_tiles.py --force       # 强制重建
"""

import argparse
import os
import sys
import time
from glob import glob

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    parser = argparse.ArgumentParser(description="多 pitch tile 渲染")
    parser.add_argument("--max-poses", type=int, default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--las", default=None)
    parser.add_argument("--output-dir", default="projections/experiments/multi_pitch")
    args = parser.parse_args()

    if os.path.abspath(args.output_dir) == os.path.abspath("projections"):
        parser.error("多 pitch 属于实验资产，--output-dir 不能指向生产 projections")

    from services.las_processor.projection_octree import (
        project_las_multi_view_octree,
        MULTI_PITCH_VIEW_DIRECTIONS,
        MULTI_PITCH_FOV_DEG,
        RELAXED_BLACK_PIXEL_THRESHOLD,
        BLACK_PIXEL_THRESHOLD,
    )

    las_path = args.las
    if not las_path:
        candidates = glob("las/*.las")
        if not candidates:
            print("ERROR: las/*.las not found")
            sys.exit(1)
        las_path = candidates[0]

    # 临时修改全局阈值
    import services.las_processor.projection_octree as poct
    original_threshold = poct.BLACK_PIXEL_THRESHOLD
    poct.BLACK_PIXEL_THRESHOLD = RELAXED_BLACK_PIXEL_THRESHOLD

    print(f"{'='*60}")
    print(f"多 pitch tile 渲染（方案 A）")
    print(f"  LAS: {las_path}")
    print(f"  pitch: -30°, -15°, 0°, +15°")
    print(f"  yaw: 8 向 × 4 pitch = {len(MULTI_PITCH_VIEW_DIRECTIONS)} 向/pose")
    print(f"  FOV: {MULTI_PITCH_FOV_DEG}°")
    print(f"  BLACK_PIXEL_THRESHOLD: {original_threshold} → {RELAXED_BLACK_PIXEL_THRESHOLD}")
    print(f"  max_poses: {args.max_poses or 'all'}")
    print(f"{'='*60}")

    def progress(msg, pct):
        print(f"[{pct:3d}%] {msg}")

    t0 = time.time()
    tiles = project_las_multi_view_octree(
        las_path=las_path,
        output_dir=args.output_dir,
        max_poses=args.max_poses,
        force_rebuild=args.force,
        view_directions=MULTI_PITCH_VIEW_DIRECTIONS,
        fov_deg=MULTI_PITCH_FOV_DEG,
        progress_callback=progress,
    )

    # 恢复阈值
    poct.BLACK_PIXEL_THRESHOLD = original_threshold

    elapsed = time.time() - t0
    accepted = [t for t in tiles if t.get("accepted")]
    print(f"\n{'='*60}")
    print(f"完成: {elapsed:.1f}s")
    print(f"  总 tile: {len(tiles)}")
    print(f"  有效 tile: {len(accepted)} ({len(accepted)/max(len(tiles),1)*100:.1f}%)")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
