import datetime
import json
import threading
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter

CST = ZoneInfo("Asia/Shanghai")

router = APIRouter(prefix="/api/las", tags=["las"])

# 全局预处理状态
_preprocess_status = {
    "running": False,
    "progress": 0,
    "step": "",
    "error": None,
    "finished_at": None,
}


def _run_preprocess():
    global _preprocess_status
    _preprocess_status["running"] = True
    _preprocess_status["progress"] = 0
    _preprocess_status["error"] = None
    _preprocess_status["finished_at"] = None

    try:
        # Step 1: 扫描 LAS 文件
        _preprocess_status["step"] = "扫描 LAS 文件..."
        _preprocess_status["progress"] = 5
        las_dir = Path("las")
        las_files = sorted(las_dir.glob("*.las"))
        if not las_files:
            _preprocess_status["error"] = "未找到 LAS 文件"
            _preprocess_status["running"] = False
            return

        las_path = str(las_files[0])
        _preprocess_status["step"] = f"处理 {las_files[0].name}..."

        # Step 2: 分块投影
        _preprocess_status["step"] = "生成分块 LAS 投影图（20m×20m→512×512）..."
        _preprocess_status["progress"] = 10
        from services.las_processor.projection import project_las_multi_view
        tiles = project_las_multi_view(las_path)
        _preprocess_status["progress"] = 70
        n_tiles = len(tiles)
        total_pixels = sum(t["pixel_count"] for t in tiles)
        _preprocess_status["step"] = (f"投影完成: {n_tiles} 张图, "
                                      f"{total_pixels} 总像素")

        # Step 3: 提取各 tile 的 SIFT 特征
        _preprocess_status["step"] = "提取各图 SIFT 特征..."
        _preprocess_status["progress"] = 75
        import cv2
        import numpy as np
        feat_dir = Path("projections")
        tile_features = {}
        for i, tile in enumerate(tiles):
            _preprocess_status["step"] = f"提取特征 {i+1}/{n_tiles}: {Path(tile['image_path']).name}"
            _preprocess_status["progress"] = 75 + int(20 * (i + 1) / n_tiles)
            proj_img = cv2.imread(tile["image_path"], cv2.IMREAD_GRAYSCALE)
            if proj_img is None:
                continue
            sift = cv2.SIFT_create(nfeatures=1000)
            kp, des = sift.detectAndCompute(proj_img, None)
            if des is not None and kp is not None:
                tile_features[Path(tile["image_path"]).stem] = {
                    "n_kp": len(kp),
                    "path": tile["image_path"],
                }

        # 保存特征索引
        with open(feat_dir / "tile_features_index.json", "w") as f:
            json.dump(tile_features, f, indent=2)

        total_kp = sum(v["n_kp"] for v in tile_features.values())
        _preprocess_status["progress"] = 95
        _preprocess_status["step"] = f"特征提取完成: {len(tile_features)} 张图, {total_kp} 特征点"

        # Step 4: 完成
        _preprocess_status["progress"] = 100
        _preprocess_status["step"] = "预处理完成"
        _preprocess_status["finished_at"] = datetime.datetime.now(CST).isoformat()

    except Exception as e:
        _preprocess_status["error"] = str(e)
        _preprocess_status["step"] = f"错误: {e}"
        import traceback
        traceback.print_exc()
    finally:
        _preprocess_status["running"] = False


@router.post("/preprocess")
def start_preprocess():
    """启动 LAS 预处理（投影 + 特征提取）"""
    if _preprocess_status["running"]:
        return {"status": "running", "message": "预处理正在进行中"}
    thread = threading.Thread(target=_run_preprocess, daemon=True)
    thread.start()
    return {"status": "started", "message": "预处理已启动"}


@router.get("/preprocess/status")
def get_preprocess_status():
    """获取预处理进度"""
    return {
        "running": _preprocess_status["running"],
        "progress": _preprocess_status["progress"],
        "step": _preprocess_status["step"],
        "error": _preprocess_status["error"],
        "finished_at": _preprocess_status["finished_at"],
    }
