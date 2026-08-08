#!/usr/bin/env python3
"""
生成 HTML 2D 单应拟合诊断报告（图像嵌入 + tile 元数据 + 像素残差）

用法：
    python scripts/generate_verify_report.py --image query_images/xxx.jpg --output reports/verify_xxx.html
    python scripts/generate_verify_report.py --image query_images/xxx.jpg --top-k 5
"""

import argparse
import base64
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def img_to_base64(path, max_size=256):
    """读取图像 → base64 data URI（缩小以嵌入 HTML）"""
    try:
        import cv2
        img = cv2.imread(path)
        if img is None:
            return None
        h, w = img.shape[:2]
        scale = min(max_size / w, max_size / h, 1.0)
        if scale < 1:
            img = cv2.resize(img, (int(w * scale), int(h * scale)))
        _, buf = cv2.imencode('.png', img)
        return 'data:image/png;base64,' + base64.b64encode(buf).decode()
    except Exception:
        return None


def parse_tile_name(name):
    """从 tile 文件名解析位姿和视角。

    格式：view_yaw135_14.9_12.0_0.5_3171_p+0.png
    → view_dir=yaw135, x=14.9, y=12.0, z=0.5, pose_id=3171, pitch=0
    """
    stem = Path(name).stem  # view_yaw135_14.9_12.0_0.5_3171_p+0
    parts = stem.split('_')
    result = {"view_dir": "?", "x": 0, "y": 0, "z": 0, "pose_id": 0, "pitch": 0, "yaw": 0}
    try:
        if len(parts) >= 2:
            result["view_dir"] = parts[1]  # yaw135
            # 解析 yaw 角度
            yaw_str = parts[1].replace("yaw", "")
            if yaw_str.isdigit():
                result["yaw"] = int(yaw_str)
        if len(parts) >= 5:
            result["x"] = float(parts[2])
            result["y"] = float(parts[3])
            result["z"] = float(parts[4])
        if len(parts) >= 6 and parts[5].isdigit():
            result["pose_id"] = int(parts[5])
        # pitch tag
        for p in parts:
            if p.startswith("p") and p[1:].lstrip("-+").isdigit():
                result["pitch"] = int(p[1:])
                break
    except (ValueError, IndexError):
        pass
    return result


def main():
    parser = argparse.ArgumentParser(description="生成 HTML 2D 单应拟合诊断报告")
    parser.add_argument("--image", required=True, help="query 图像路径")
    parser.add_argument("--output", default=None, help="输出 HTML 路径（默认 reports/verify_<name>.html）")
    parser.add_argument("--top-k", type=int, default=5, help="展示前 K 个 tile")
    parser.add_argument("--n-samples", type=int, default=10, help="每 tile 采样点数")
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

    # query 缩略图
    query_b64 = img_to_base64(args.image, max_size=200)

    # 只读取当前 accepted MapTile；磁盘中的历史实验图不属于发布集合。
    all_tiles = load_published_tile_images()

    results = []
    t0 = time.time()
    for tile_path in all_tiles[:50]:
        tile = cv2.imread(tile_path)
        if tile is None:
            continue

        kq, kt, cert = _lightglue_match(q, tile)
        if len(kq) < 6:
            continue

        mask = cert > 0.003
        if mask.sum() < 6:
            continue
        kq_f, kt_f = kq[mask], kt[mask]

        H, inl_mask = compute_homography(kq_f, kt_f, reproj_thresh=5.0)
        if H is None:
            continue
        n_inliers = int(inl_mask.ravel().sum()) if inl_mask is not None else 0
        if n_inliers < 4:
            continue

        inlier_pts_q = kq_f[inl_mask.ravel() > 0] if inl_mask is not None else kq_f
        inlier_pts_t = kt_f[inl_mask.ravel() > 0] if inl_mask is not None else kt_f

        rng = np.random.RandomState(42)
        n_samp = min(args.n_samples, len(inlier_pts_q))
        if len(inlier_pts_q) > n_samp:
            idx = rng.choice(len(inlier_pts_q), n_samp, replace=False)
            sample_q = inlier_pts_q[idx]
            sample_t = inlier_pts_t[idx]
        else:
            sample_q = inlier_pts_q
            sample_t = inlier_pts_t

        predicted = cv2.perspectiveTransform(
            sample_q.astype(np.float32).reshape(-1, 1, 2), H
        ).reshape(-1, 2)
        samples = []
        for pt_q, pt_t, pt_h in zip(sample_q, sample_t, predicted):
            u_norm = float(pt_q[0] / w_q)
            v_norm = float(pt_q[1] / h_q)
            residual_px = float(np.linalg.norm(pt_h - pt_t))
            samples.append({
                "u": round(u_norm, 4),
                "v": round(v_norm, 4),
                "tile_pixel_direct": np.round(pt_t, 3).tolist(),
                "tile_pixel_via_h": np.round(pt_h, 3).tolist(),
                "residual_px": round(residual_px, 3),
            })

        if samples:
            residuals = [s["residual_px"] for s in samples]
            tile_b64 = img_to_base64(tile_path, max_size=200)
            info = parse_tile_name(os.path.basename(tile_path))
            results.append({
                "tile": os.path.basename(tile_path),
                "tile_b64": tile_b64,
                "matches": len(kq_f),
                "inliers": n_inliers,
                "n_samples": len(samples),
                "mean_residual_px": round(float(np.mean(residuals)), 3),
                "median_residual_px": round(float(np.median(residuals)), 3),
                "max_residual_px": round(float(np.max(residuals)), 3),
                "pose": info,
                "samples": samples,
            })

    elapsed = time.time() - t0
    results.sort(key=lambda x: (-x["inliers"], x["median_residual_px"]))

    # 生成 HTML
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>2D 单应拟合诊断 — {os.path.basename(args.image)}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 0; padding: 1rem; background: #f5f5f5; }}
.container {{ max-width: 1100px; margin: 0 auto; }}
h1 {{ color: #333; border-bottom: 2px solid #1976d2; padding-bottom: 0.5rem; }}
.summary {{ background: #fff; padding: 1rem; border-radius: 8px; margin: 1rem 0; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
.limitation {{ background: #fff8e1; border-left: 4px solid #ff9800; padding: 0.8rem; border-radius: 4px; }}
.tile-card {{ background: #fff; padding: 1rem; border-radius: 8px; margin: 1rem 0; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
.tile-header {{ display: flex; align-items: center; gap: 1rem; margin-bottom: 0.5rem; }}
.tile-img {{ border: 1px solid #ddd; border-radius: 4px; }}
.pose-info {{ font-size: 0.85rem; color: #555; background: #f8f9fa; padding: 0.5rem; border-radius: 4px; }}
.error-ok {{ color: #4caf50; font-weight: bold; }}
.error-warn {{ color: #ff9800; font-weight: bold; }}
.error-bad {{ color: #f44336; font-weight: bold; }}
table {{ font-size: 0.78rem; width: 100%; border-collapse: collapse; margin-top: 0.5rem; }}
th, td {{ padding: 4px 8px; border: 1px solid #eee; text-align: center; }}
th {{ background: #f5f5f5; }}
.sample-table {{ font-size: 0.72rem; }}
</style>
</head>
<body>
<div class="container">
<h1>📐 2D 单应拟合诊断（非 Benchmark）</h1>
<div class="summary">
  <p><b>Query 图像:</b> {os.path.basename(args.image)} ({query.shape[1]}×{query.shape[0]})</p>
  <p><b>生成时间:</b> {time.strftime('%Y-%m-%d %H:%M:%S')}</p>
  <p><b>验证耗时:</b> {elapsed:.1f}s</p>
  <p><b>有效 tile:</b> {len(results)} 个</p>
  {f'<img src="{query_b64}" style="max-width:200px;border-radius:4px;border:1px solid #ddd">' if query_b64 else ''}
</div>
<div class="limitation">
  <b>指标限制：</b>本报告只计算匹配点与单应预测的像素残差。
  同源 NPY 不能作为米制验证；未加载独立 holdout 位姿真值，因此这不是 Benchmark 精度报告。
</div>
<h2>前 {min(args.top_k, len(results))} 个最佳 tile</h2>
"""

    for i, r in enumerate(results[:args.top_k]):
        err_class = "error-ok" if r["median_residual_px"] < 2 else ("error-warn" if r["median_residual_px"] < 5 else "error-bad")
        p = r["pose"]
        html += f"""
<div class="tile-card">
  <div class="tile-header">
    <div>
      <h3 style="margin:0">#{i+1} {r['tile']}</h3>
      <p style="margin:0.2rem 0;font-size:0.85rem">
        内点: <b>{r['inliers']}/{r['matches']}</b> |
        内点中位残差: <b class="{err_class}">{r['median_residual_px']:.3f} px</b> |
        最大残差: <b>{r['max_residual_px']:.3f} px</b>
      </p>
    </div>
    {f'<img src="{r["tile_b64"]}" class="tile-img" width="128">' if r.get("tile_b64") else ''}
  </div>
  <div class="pose-info">
    <b>位姿:</b> x={p['x']:.1f}, y={p['y']:.1f}, z={p['z']:.1f} |
    <b>视角:</b> yaw={p['yaw']}°, pitch={p['pitch']}° ({p['view_dir']}) |
    <b>pose_id:</b> {p['pose_id']}
  </div>
  <details open>
    <summary style="cursor:pointer;font-size:0.85rem;color:#1976d2;margin-top:0.5rem">采样点像素残差（{r['n_samples']} 个）</summary>
    <table class="sample-table">
      <tr><th>u</th><th>v</th><th>tile 直接匹配像素</th><th>单应预测像素</th><th>残差</th></tr>
"""
        for s in r["samples"]:
            ec = "error-ok" if s["residual_px"] < 2 else ("error-warn" if s["residual_px"] < 5 else "error-bad")
            html += f"""      <tr>
        <td>{s['u']:.4f}</td><td>{s['v']:.4f}</td>
        <td style="font-family:monospace">[{', '.join(f'{v:.2f}' for v in s['tile_pixel_direct'])}]</td>
        <td style="font-family:monospace">[{', '.join(f'{v:.2f}' for v in s['tile_pixel_via_h'])}]</td>
        <td class="{ec}">{s['residual_px']:.3f}px</td>
      </tr>
"""
        html += """    </table>
  </details>
</div>
"""

    html += """
</div>
</body>
</html>"""

    # 保存
    out_path = args.output
    if not out_path:
        name = Path(args.image).stem[:8]
        out_path = f"reports/generated/verify_{name}.html"
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"报告已保存: {out_path}")
    print(f"  有效 tile: {len(results)}")
    if results:
        print(f"  最佳: {results[0]['tile']} — 中位残差 {results[0]['median_residual_px']:.3f}px")


if __name__ == "__main__":
    main()
