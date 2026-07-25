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
    "mode": None,
}


def _run_preprocess(mode: str = "full"):
    global _preprocess_status
    _preprocess_status["running"] = True
    _preprocess_status["progress"] = 0
    _preprocess_status["error"] = None
    _preprocess_status["finished_at"] = None
    _preprocess_status["mode"] = mode

    try:
        _preprocess_status["step"] = "扫描 LAS 文件..."
        _preprocess_status["progress"] = 5
        las_dir = Path("las")
        las_files = sorted(las_dir.glob("*.las"))
        las_files = [f for f in las_files if "subsample" not in f.name]
        if not las_files:
            _preprocess_status["error"] = "未找到 LAS 文件"
            _preprocess_status["running"] = False
            return

        las_path = str(las_files[0])
        _preprocess_status["step"] = f"处理 {las_files[0].name}..."

        out_dir = Path("projections")
        out_dir.mkdir(parents=True, exist_ok=True)

        def _progress_callback(step, progress):
            _preprocess_status["step"] = step
            _preprocess_status["progress"] = progress

        if mode in {"build", "full"}:
            from services.las_processor.projection_octree import _prepare_downsampled_las, build_octree

            _preprocess_status["step"] = "下采样 LAS..."
            _preprocess_status["progress"] = 10
            sampled_las_path = _prepare_downsampled_las(las_path, str(out_dir), force=True)

            _preprocess_status["step"] = "构建 Octree 八叉树..."
            _preprocess_status["progress"] = 20
            build_octree(sampled_las_path, str(out_dir), force=True)

            if mode == "build":
                _preprocess_status["progress"] = 100
                _preprocess_status["step"] = "下采样 + octree_build 完成"
                _preprocess_status["finished_at"] = datetime.datetime.now(CST).isoformat()
                return

        if mode in {"render", "full"}:
            from services.las_processor.projection_octree import project_las_multi_view_octree

            _preprocess_status["step"] = "渲染投影图与坐标映射..."
            _preprocess_status["progress"] = 30
            # render 模式强制重绘（法线图算法等更新时需要刷新）
            force_rebuild = (mode == "render")
            tiles = project_las_multi_view_octree(
                las_path,
                output_dir=str(out_dir),
                max_poses=None,
                force_rebuild=force_rebuild,
                progress_callback=_progress_callback,
            )
            n_tiles = len(tiles)
            total_pixels = sum(t.get("pixel_count", 0) for t in tiles)
            _preprocess_status["step"] = (f"Octree 投影完成: {n_tiles} 张图, {total_pixels} 总像素")
            _preprocess_status["progress"] = 95

            if mode == "render":
                _preprocess_status["progress"] = 100
                _preprocess_status["step"] = f"投影完成: {n_tiles} 张图"
                _preprocess_status["finished_at"] = datetime.datetime.now(CST).isoformat()
                return

        if mode in {"feature", "full"}:
            # feature 模式下 tiles 已存在，从 tile_index 读取数量
            if mode == "feature":
                try:
                    with open("projections/tile_index.json") as _f:
                        n_tiles = len(json.load(_f))
                except Exception:
                    n_tiles = 0
                    _preprocess_status["error"] = "tile_index.json 不存在，请先渲染投影图"
                    _preprocess_status["running"] = False
                    return

            _preprocess_status["step"] = "提取各图 SALAD 特征 (DINOv2)..."
            _preprocess_status["progress"] = 75
            from services.localizer.salad_roma import _build_salad_index

            def _salad_progress_callback(processed, total, elapsed):
                _preprocess_status["step"] = f"提取 SALAD 特征 {processed}/{total}, 已耗时 {elapsed:.1f}s"
                _preprocess_status["progress"] = 75 + int(20 * processed / total)

            _build_salad_index(force_rebuild=True, progress_callback=_salad_progress_callback)

            _preprocess_status["progress"] = 95
            _preprocess_status["step"] = f"SALAD 特征提取完成: {n_tiles} 张图"

        if mode == "ace":
            _preprocess_status["step"] = "训练 ACE 场景坐标回归模型..."
            _preprocess_status["progress"] = 50
            try:
                from services.localizer.ace_trainer import train_ace_model
                # 检查 tiles 是否存在
                if not os.path.exists("projections/tile_index.json"):
                    _preprocess_status["error"] = "tile_index.json 不存在，请先渲染投影图"
                    _preprocess_status["running"] = False
                    return
                train_ace_model(epochs=10)
                _preprocess_status["step"] = "ACE 模型训练完成"
            except Exception as ace_err:
                _preprocess_status["error"] = f"ACE 训练失败: {ace_err}"
                _preprocess_status["running"] = False
                return
            _preprocess_status["progress"] = 100
            _preprocess_status["step"] = f"SALAD 特征提取完成: {n_tiles} 张图"

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


def _start_preprocess(mode: str):
    if _preprocess_status["running"]:
        return {"status": "running", "message": "预处理正在进行中"}
    try:
        thread = threading.Thread(target=_run_preprocess, args=(mode,), daemon=True)
        thread.start()
        return {"status": "started", "message": "预处理已启动"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/preprocess")
def start_preprocess():
    """启动完整 LAS 预处理（下采样 + octree_build + 渲染 + 特征提取）"""
    return _start_preprocess("full")


@router.post("/preprocess/build")
def start_preprocess_build():
    """启动下采样 + octree_build 流程"""
    return _start_preprocess("build")


@router.post("/preprocess/render")
def start_preprocess_render():
    """仅渲染投影图（不提取特征）"""
    return _start_preprocess("render")


@router.post("/preprocess/feature")
def start_preprocess_feature():
    """仅重建 SALAD 特征库（需 tiles 已存在）"""
    return _start_preprocess("feature")


@router.post("/preprocess/ace")
def start_preprocess_ace():
    """仅训练 ACE 模型（需 tiles 已存在）"""
    return _start_preprocess("ace")


@router.get("/preprocess/status")
def get_preprocess_status():
    """获取预处理进度"""
    return {
        "running": _preprocess_status["running"],
        "progress": _preprocess_status["progress"],
        "step": _preprocess_status["step"],
        "error": _preprocess_status["error"],
        "finished_at": _preprocess_status["finished_at"],
        "mode": _preprocess_status["mode"],
    }
