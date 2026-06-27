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

        # Step 2: 投影
        _preprocess_status["step"] = "生成 LAS 投影图..."
        _preprocess_status["progress"] = 10
        from services.las_processor.projection import project_las_to_image
        result = project_las_to_image(las_path, max_points=5_000_000)
        _preprocess_status["progress"] = 60
        _preprocess_status["step"] = (f"投影完成: {result['width']}x{result['height']}, "
                                      f"{result['pixel_count']} 像素")

        # Step 3: 提取 SIFT 特征
        _preprocess_status["step"] = "提取 SIFT 特征..."
        _preprocess_status["progress"] = 70
        import cv2
        import numpy as np
        proj_img = cv2.imread(result["image_path"], cv2.IMREAD_GRAYSCALE)
        sift = cv2.SIFT_create(nfeatures=5000)
        kp, des = sift.detectAndCompute(proj_img, None)
        n_kp = len(kp) if kp is not None else 0

        # 保存特征到文件
        feat_dir = Path("projections")
        if des is not None:
            np.savez_compressed(
                str(feat_dir / "las_features.npz"),
                keypoints=np.array([p.pt for p in kp]),
                descriptors=des,
                angles=np.array([p.angle for p in kp]),
                sizes=np.array([p.size for p in kp]),
            )
        _preprocess_status["progress"] = 90
        _preprocess_status["step"] = f"特征提取完成: {n_kp} 个特征点"

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
