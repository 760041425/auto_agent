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
        # 排除旧的 subsample 文件
        las_files = [f for f in las_files if "subsample" not in f.name]
        if not las_files:
            _preprocess_status["error"] = "未找到 LAS 文件"
            _preprocess_status["running"] = False
            return

        las_path = str(las_files[0])
        _preprocess_status["step"] = f"处理 {las_files[0].name}..."

        # Step 2: Octree 多视角投影
        _preprocess_status["step"] = "构建 Octree 八叉树..."
        _preprocess_status["progress"] = 10
        from services.las_processor.projection_octree import project_las_multi_view_octree
        out_dir = Path("projections")
        if out_dir.exists():
            for child in out_dir.iterdir():
                if child.is_file() and child.name not in {"tile_features_index.json", "tile_index.json", "projection_view_poses.json"}:
                    child.unlink()
                elif child.is_dir() and child.name != "octree_data":
                    import shutil
                    shutil.rmtree(child, ignore_errors=True)
        out_dir.mkdir(parents=True, exist_ok=True)

        def _progress_callback(step, progress):
            _preprocess_status["step"] = step
            _preprocess_status["progress"] = progress

        tiles = project_las_multi_view_octree(
            las_path,
            output_dir=str(out_dir),
            max_poses=None,
            force_rebuild=True,
            progress_callback=_progress_callback,
        )
        n_tiles = len(tiles)
        total_pixels = sum(t.get("pixel_count", 0) for t in tiles)
        _preprocess_status["step"] = (f"Octree 投影完成: {n_tiles} 张图, "
                                      f"{total_pixels} 总像素")

        # Step 3: 提取各 tile 的 SALAD 特征（DINOv2 全局描述子）
        _preprocess_status["step"] = "提取各图 SALAD 特征 (DINOv2)..."
        _preprocess_status["progress"] = 75
        
        from services.localizer.salad_roma import _build_salad_index
        
        def _salad_progress_callback(processed, total, elapsed):
            _preprocess_status["step"] = f"提取 SALAD 特征 {processed}/{total}, 已耗时 {elapsed:.1f}s"
            _preprocess_status["progress"] = 75 + int(20 * processed / total)
        
        _build_salad_index(force_rebuild=True, progress_callback=_salad_progress_callback)
        
        _preprocess_status["progress"] = 95
        _preprocess_status["step"] = f"SALAD 特征提取完成: {n_tiles} 张图"

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
    try:
        thread = threading.Thread(target=_run_preprocess, daemon=True)
        thread.start()
        return {"status": "started", "message": "预处理已启动"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


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
