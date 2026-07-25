import datetime
import threading
import os
import logging
import json
import cv2
import numpy as np
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.database import get_db, SessionLocal
from api.models import ImageModel, TaskModel

CST = ZoneInfo("Asia/Shanghai")

router = APIRouter(prefix="/api/localize", tags=["localize"])

ALGORITHMS = {
    "salad_roma": {"feature": "dino", "matcher": "salad_roma", "label": "SALAD+RoMa (v3)"},
    "flann": {"feature": "sift", "matcher": "flann", "label": "SIFT + FLANN"},
    "bf": {"feature": "sift", "matcher": "bf", "label": "SIFT + BruteForce"},
    "flann_lowes": {"feature": "sift", "matcher": "flann_lowes", "label": "SIFT + FLANN (严格)"},
    "bf_cross": {"feature": "sift", "matcher": "bf_cross", "label": "SIFT + BF 交叉验证"},
    "knn_rank": {"feature": "sift", "matcher": "knn_rank", "label": "SIFT + KNN Rank"},
    "lightglue": {"feature": "sift", "matcher": "lightglue", "label": "SIFT + LightGlue"},
    "loftr": {"feature": "sift", "matcher": "loftr", "label": "SIFT + LoFTR"},
}
_queued_task_ids: set[int] = set()
_queue_lock = threading.Lock()

def log(msg: str):
    print(f"[LOCALIZE] {msg}")


class LocalizeRequest(BaseModel):
    image_id: int
    algorithms: list[str] = ["salad_roma"]
    # 兼容旧页面/API；新调用请传 algorithms。
    feature_methods: Optional[list[str]] = None
    match_methods: Optional[list[str]] = None
    max_iterations: int = 2
    debug_visualizations: bool = False


class LocalizeResult(BaseModel):
    feature_method: str
    match_method: str
    success: bool
    pose: Optional[dict] = None
    inliers: int = 0
    comparison_image: Optional[str] = None
    reprojection_image: Optional[str] = None
    matched_points: list = []


def _append_result(results, fm, mm, result):
    """统一格式化 localize 结果"""
    comp = result.get("comparison_image")
    reproj = result.get("reprojection_image")
    
    if comp:
        comp = comp.replace("projections/", "")
        comp = f"/projections/{comp}"
    if reproj:
        reproj = reproj.replace("projections/", "")
        reproj = f"/projections/{reproj}"
    
    results.append({
        "feature_method": fm,
        "match_method": mm,
        "success": result.get("success", False),
        "pose": result.get("pose"),
        "inliers": result.get("inliers", 0),
        "comparison_image": comp,
        "reprojection_image": reproj,
        "matched_points": result.get("matched_points", []),
        "total_rounds": result.get("total_rounds"),
        "iter_history": result.get("iter_history", []),
        "total_3d_points": result.get("total_3d_points", 0),
        "all_candidates": result.get("all_candidates", []),
    })


def _normalize_algorithms(req: LocalizeRequest) -> list[str]:
    selected = req.algorithms or []
    if req.match_methods is not None:  # legacy request takes precedence when supplied
        selected = req.match_methods
    selected = list(dict.fromkeys(selected))
    invalid = [name for name in selected if name not in ALGORITHMS]
    if invalid:
        raise HTTPException(422, f"不支持的定位算法: {', '.join(invalid)}")
    if not selected:
        raise HTTPException(422, "至少选择一种定位算法")
    return selected


def _queue_localize_task(task_id: int):
    with _queue_lock:
        if task_id in _queued_task_ids:
            return
        _queued_task_ids.add(task_id)
    threading.Thread(target=run_localize_task, args=(task_id,), daemon=True).start()


def run_localize_task(task_id: int):
    db = SessionLocal()
    try:
        task = db.query(TaskModel).filter(TaskModel.id == task_id).first()
        if not task:
            return
        request = task.request_json or {}
        algorithms = request.get("algorithms", ["salad_roma"])
        max_iterations = max(1, min(int(request.get("max_iterations", 2)), 10))
        debug_visualizations = bool(request.get("debug_visualizations", False))
        task.status = "running"
        task.result_json = {"results": [], "total": len(algorithms)}
        db.commit()

        from services.localizer import load_colmap, localize_image

        load_colmap()

        img = db.query(ImageModel).filter(ImageModel.id == task.image_id).first()
        if not img:
            task.status = "failed"
            task.error_message = "Image not found"
            db.commit()
            return

        results = []
        out_dir = f"projections/localize/task_{task_id}"
        Path(out_dir).mkdir(parents=True, exist_ok=True)

        for algorithm in algorithms:
            config = ALGORITHMS[algorithm]
            log(f"  定位算法: {config['label']}")
            try:
                result = localize_image(
                    img.path, output_dir=out_dir,
                    feature_method=config["feature"], match_method=config["matcher"],
                    max_iterations=max_iterations, debug_visualizations=debug_visualizations,
                )
                _append_result(results, config["feature"], algorithm, result)
            except Exception as e:
                log(f"    算法 {algorithm} 失败: {e}")
                _append_result(results, config["feature"], algorithm, {"success": False, "error": str(e)})
            task.result_json = {"results": results, "total": len(algorithms)}
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(task, "result_json")
            db.commit()

        task.result_json = {"results": results, "total": len(algorithms)}
        task.status = "completed"
        task.finished_at = datetime.datetime.now(CST)
        db.commit()
    except Exception as e:
        task.status = "failed"
        task.error_message = str(e)
        db.commit()
    finally:
        db.close()
        with _queue_lock:
            _queued_task_ids.discard(task_id)


@router.get("/check")
def check_colmap():
    """检查 COLMAP 数据是否可用"""
    import os
    has_images = os.path.exists("las/images.txt")
    has_points = os.path.exists("las/points3D.txt")
    has_las = len([f for f in os.listdir("las") if f.endswith(".las")]) > 0
    return {
        "available": has_images and has_points,
        "has_images": has_images,
        "has_points": has_points,
        "has_las": has_las,
    }


@router.post("")
def create_localize_task(req: LocalizeRequest, db: Session = Depends(get_db)):
    if not db.query(ImageModel).filter(ImageModel.id == req.image_id).first():
        raise HTTPException(404, "Image not found")
    algorithms = _normalize_algorithms(req)
    task = TaskModel(
        image_id=req.image_id, status="pending", task_type="localize",
        request_json={
            "algorithms": algorithms,
            "max_iterations": max(1, min(req.max_iterations, 10)),
            "debug_visualizations": req.debug_visualizations,
        },
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    _queue_localize_task(task.id)

    return {"task_id": task.id}


def resume_pending_localize_tasks():
    """启动时恢复被中断的定位任务。"""
    db = SessionLocal()
    try:
        tasks = db.query(TaskModel).filter(
            TaskModel.task_type == "localize", TaskModel.status.in_(["pending", "running"])
        ).all()
        for task in tasks:
            task.status = "pending"
            _queue_localize_task(task.id)
        db.commit()
    finally:
        db.close()


@router.get("/{task_id}")
def get_localize_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(TaskModel).filter(TaskModel.id == task_id).first()
    if not task:
        raise HTTPException(404, "Task not found")

    if task.status == "running":
        return {"status": "running", "results": []}
    if task.status == "failed":
        return {"status": "failed", "results": [], "error": task.error_message}

    results = []
    if task.result_json and "results" in task.result_json:
        results = task.result_json["results"]

    return {"status": "completed", "results": results}


class RefineRequest(BaseModel):
    task_id: int
    method_index: int = 0  # 用哪个 matcher 的位姿做优化，默认第一个


@router.post("/refine")
def refine_pose(req: RefineRequest, db: Session = Depends(get_db)):
    """在已有定位结果的位姿基础上，用 RoMa 做一轮 PnP 优化"""
    task = db.query(TaskModel).filter(TaskModel.id == req.task_id).first()
    if not task:
        raise HTTPException(404, "Task not found")
    if not task.result_json or "results" not in task.result_json:
        raise HTTPException(400, "No results to refine")
    
    results = task.result_json["results"]
    if req.method_index >= len(results):
        raise HTTPException(400, f"Method index {req.method_index} out of range")
    
    target = results[req.method_index]
    if not target.get("success"):
        raise HTTPException(400, "Selected method did not succeed")
    
    pose = target.get("pose")
    if not pose:
        raise HTTPException(400, "No pose to refine")
    
    from services.localizer.salad_roma import refine_pose_with_roma
    from services.localizer import load_colmap, get_point_cloud_arrays
    from services.las_processor.projection import _load_poses_and_offset
    import cv2, numpy as np
    from services.localizer import _get_camera_matrix
    
    known_points, _ = load_colmap()
    all_pts, all_col = get_point_cloud_arrays()
    
    # 找对应的查询图
    from api.models import ImageModel
    img = db.query(ImageModel).filter(ImageModel.id == task.image_id).first()
    if not img:
        raise HTTPException(404, "Image not found")
    
    img_path = os.path.abspath(img.path)
    if not os.path.exists(img_path):
        raise HTTPException(400, f"图像文件不存在: {img_path}")
    
    q_img = cv2.imread(img_path)
    if q_img is None:
        raise HTTPException(400, f"无法读取图像: {img_path}")
    h, w = q_img.shape[:2]
    scale = 512 / max(h, w)
    q_w = int(w * scale)
    q_h = int(h * scale)
    
    cm = _get_camera_matrix(w, h, fov_deg=75)
    cm[0,0] *= scale; cm[1,1] *= scale
    cm[0,2] *= scale; cm[1,2] *= scale
    
    q = pose["quaternion"]
    t = pose["translation"]
    # quaternion -> rvec
    from scipy.spatial.transform import Rotation
    r = Rotation.from_quat([q[1], q[2], q[3], q[0]])  # xyzw -> wxyz
    rvec = cv2.Rodrigues(r.as_matrix())[0]
    tvec = np.array(t, dtype=np.float64).reshape(3, 1)
    
    out_dir = f"projections/localize/task_{task.id}"
    
    refine_result = refine_pose_with_roma(
        img.path, rvec, tvec, cm, q_w, q_h,
        all_pts, all_col, out_dir=out_dir,
    )
    
    if not refine_result["success"]:
        return {"success": False, "error": refine_result.get("error", "Refine failed")}
    
    # 更新位姿
    new_rvec = refine_result["rvec"]
    new_tvec = refine_result["tvec"]
    rmat, _ = cv2.Rodrigues(new_rvec)
    from services.localizer import _rotation_matrix_to_quaternion
    new_quat = _rotation_matrix_to_quaternion(rmat)
    
    updated_pose = {
        "quaternion": [float(q) for q in new_quat],
        "translation": new_tvec.flatten().tolist(),
    }
    
    # 持久化优化结果：更新 task result_json 中对应 matcher 的位姿
    from sqlalchemy.orm.attributes import flag_modified
    target["pose"] = updated_pose
    target["inliers"] = refine_result["inliers"]
    
    # 重新生成对比图（用新位姿渲染）
    try:
        from services.localizer.salad_roma import _render_comparison as salad_render
        from services.localizer import _render_results as sift_render
        from services.localizer import load_colmap, _get_camera_matrix
        
        known_points, _ = load_colmap()
        out_dir = Path(f"projections/localize/task_{task.id}")
        q_small = cv2.resize(q_img, (q_w, q_h))
        
        if target.get("match_method") == "salad_roma":
            # 直接用 salad_roma 的渲染函数
            result = salad_render(
                new_rvec, new_tvec, refine_result["inliers"],
                [], [], known_points, cm, q_w, q_h, q_small,
                out_dir, "salad_roma", q_small,
            )
        else:
            result = sift_render(
                new_rvec, new_tvec, refine_result["inliers"],
                [], [], known_points, cm, q_w, q_h, q_small,
                out_dir, target.get("match_method", "refine"),
            )
        
        new_comp = result.get("comparison_image")
        new_reproj = result.get("reprojection_image")
        if new_comp:
            target["comparison_image"] = f"/{new_comp}" if not str(new_comp).startswith("/") else new_comp
        if new_reproj:
            target["reprojection_image"] = f"/{new_reproj}" if not str(new_reproj).startswith("/") else new_reproj
    except Exception as e:
        import traceback
        traceback.print_exc()
        # 渲染失败不影响主流程
    
    flag_modified(task, "result_json")
    db.commit()
    
    return {
        "success": True,
        "pose_before": pose,
        "pose_after": updated_pose,
        "inliers": refine_result["inliers"],
        "salad_sim_before": refine_result.get("salad_sim_before", 0),
        "salad_sim_after": refine_result.get("salad_sim_after", 0),
        "improved": refine_result.get("improved", False),
    }


@router.post("/localize/ace")
async def ace_localize_endpoint(image_id: int = Form(...), db: Session = Depends(get_db)):
    """ACE 单次定位（场景坐标回归 + PnP，无需多轮迭代）"""
    from services.localizer.ace_trainer import ace_localize, CoordRegression
    import torch

    # 1. 加载模型（首次加载后缓存）
    model_path = "projections/ace_model.pth"
    if not os.path.exists(model_path):
        return {"success": False, "error": "ACE 模型未训练，请先执行训练"}

    if not hasattr(ace_localize_endpoint, "_model"):
        model = CoordRegression(in_channels=6).to("cpu")
        model.load_state_dict(torch.load(model_path, map_location="cpu", weights_only=True))
        model.eval()
        ace_localize_endpoint._model = model

    # 2. 加载查询图像
    img = db.query(ImageModel).filter(ImageModel.id == image_id).first()
    if not img or not os.path.exists(img.path):
        return {"success": False, "error": "图像不存在"}

    image = cv2.imread(img.path)
    if image is None:
        return {"success": False, "error": "无法读取图像"}

    # 3. 相机内参（与渲染时一致）
    h, w = image.shape[:2]
    fov_deg = 75
    f = max(w, h) / (2 * np.tan(np.deg2rad(fov_deg / 2)))
    K = np.array([[f, 0, w/2], [0, f, h/2], [0, 0, 1]])

    # 4. ACE 定位（无 normal map，用零填充）
    success, rvec, tvec, _ = ace_localize(
        ace_localize_endpoint._model, image, K, normal_map=None,
    )

    if not success:
        return {"success": False, "error": "ACE 定位失败"}

    # 5. 转 COLMAP 格式返回
    from scipy.spatial.transform import Rotation as R
    rot_mat, _ = cv2.Rodrigues(rvec)
    q = R.from_matrix(rot_mat).as_quat()  # [x, y, z, w]

    return {
        "success": True,
        "method": "ace",
        "position": [float(tvec[0]), float(tvec[1]), float(tvec[2])],
        "quaternion": [float(q[3]), float(q[0]), float(q[1]), float(q[2])],  # w, x, y, z
    }
