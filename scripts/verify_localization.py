#!/usr/bin/env python3
"""
定位结果 2D 单应拟合诊断脚本（非 Benchmark）

流程：
1. 取 query 图，resize 到 512×512
2. 对每个候选 tile，DISK+LightGlue 匹配 → 计算单应性矩阵 H
3. 在单应内点上比较 tile 直接匹配像素与 H 预测像素
4. 输出像素残差统计；同源 NPY 不再输出米制差异

独立位姿精度必须由 ``benchmark_localizers.py --ground-truth`` 提供。

用法：
    python scripts/verify_localization.py --image query_images/xxx.jpg
    python scripts/verify_localization.py --image query_images/xxx.jpg --top-k 5
"""

import argparse
import sys
import os
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    parser = argparse.ArgumentParser(description="验证定位投影正确性")
    parser.add_argument("--image", required=True, help="query 图像路径")
    parser.add_argument("--top-k", type=int, default=5, help="验证前 K 个最佳 tile")
    parser.add_argument("--n-samples", type=int, default=30, help="每 tile 采样点数")
    parser.add_argument("--cert-thresh", type=float, default=0.003, help="LightGlue 置信度阈值")
    args = parser.parse_args()

    import cv2
    import numpy as np
    import torch
    from services.localizer.salad_roma import _get_lightglue_model, _lightglue_match
    from services.localizer.verify_projection import compute_homography, load_published_tile_images

    device = torch.device("mps") if torch.backends.mps.is_available() else torch.device("cpu")
    _get_lightglue_model(device=device)

    query = cv2.imread(args.image)
    if query is None:
        print(f"ERROR: cannot read {args.image}")
        sys.exit(1)
    q = cv2.resize(query, (512, 512))
    h_q, w_q = q.shape[:2]

    # 只读取当前 accepted MapTile；磁盘中的历史多 pitch/水平实验图不得参与。
    all_tiles = load_published_tile_images()
    print(f"query: {args.image} ({query.shape[1]}×{query.shape[0]})")
    print(f"published tiles: {len(all_tiles)}")

    results = []
    t0 = time.time()
    for ti, tile_path in enumerate(all_tiles[:50]):  # 限制前 50 个
        tile = cv2.imread(tile_path)
        if tile is None:
            continue

        kq, kt, cert = _lightglue_match(q, tile)
        if len(kq) < 6:
            continue

        mask = cert > args.cert_thresh
        if mask.sum() < 6:
            continue
        kq_f, kt_f = kq[mask], kt[mask]

        H, inl_mask = compute_homography(kq_f, kt_f, reproj_thresh=5.0)
        if H is None:
            continue
        n_inliers = int(inl_mask.ravel().sum()) if inl_mask is not None else 0
        if n_inliers < 4:
            continue

        # 采样 query 图上的归一ized 坐标（用内点）
        rng = np.random.RandomState(42)
        inlier_pts_q = kq_f[inl_mask.ravel() > 0] if inl_mask is not None else kq_f
        inlier_pts_t = kt_f[inl_mask.ravel() > 0] if inl_mask is not None else kt_f
        if len(inlier_pts_q) > args.n_samples:
            idx = rng.choice(len(inlier_pts_q), args.n_samples, replace=False)
            sample_q = inlier_pts_q[idx]
            sample_t = inlier_pts_t[idx]
        else:
            sample_q = inlier_pts_q
            sample_t = inlier_pts_t

        predicted = cv2.perspectiveTransform(
            sample_q.astype(np.float32).reshape(-1, 1, 2), H
        ).reshape(-1, 2)
        sample_details = []
        for pt_q, pt_t, pt_h in zip(sample_q, sample_t, predicted):
            u_norm = float(pt_q[0] / w_q)
            v_norm = float(pt_q[1] / h_q)
            residual_px = float(np.linalg.norm(pt_h - pt_t))
            sample_details.append({
                "u": round(u_norm, 4),
                "v": round(v_norm, 4),
                "tile_pixel_direct": np.round(pt_t, 3).tolist(),
                "tile_pixel_via_h": np.round(pt_h, 3).tolist(),
                "residual_px": round(residual_px, 3),
            })

        if sample_details:
            residuals = [s["residual_px"] for s in sample_details]
            results.append({
                "tile": os.path.basename(tile_path),
                "matches": len(kq_f),
                "inliers": n_inliers,
                "n_samples": len(sample_details),
                "mean_residual_px": round(float(np.mean(residuals)), 3),
                "median_residual_px": round(float(np.median(residuals)), 3),
                "max_residual_px": round(float(np.max(residuals)), 3),
                "samples": sample_details[:5],
            })

    elapsed = time.time() - t0
    results.sort(key=lambda x: (-x["inliers"], x.get("mean_residual_px", 0)))
    print(f"\n{'='*60}")
    print(f"验证完成: {elapsed:.1f}s, {len(results)} tiles passed")
    print(f"{'='*60}")
    for r in results[:args.top_k]:
        print(f"  {r['tile']}: {r['inliers']}/{r['matches']} inliers, "
              f"{r['n_samples']} samples, "
              f"mean_residual={r.get('mean_residual_px', 'n/a')}px, "
              f"median_residual={r.get('median_residual_px', 'n/a')}px")
        if r.get("samples"):
            for s in r["samples"][:3]:
                print(f"    u={s['u']:.3f} v={s['v']:.3f} "
                      f"tile_direct={s['tile_pixel_direct']} "
                      f"tile_via_H={s['tile_pixel_via_h']} "
                      f"residual={s['residual_px']:.3f}px")

    # 保存详细结果
    out_path = "projections/verify_result.json"
    with open(out_path, "w") as f:
        json.dump({"query": args.image, "results": results}, f, indent=2)
    print(f"\n详细结果: {out_path}")


if __name__ == "__main__":
    main()
