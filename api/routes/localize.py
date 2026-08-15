import datetime
import threading
import os
import subprocess
import sys
import time
import cv2
import numpy as np
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from api.database import get_db, SessionLocal
from api.models import ImageModel, TaskModel
from services.localizer.contracts import normalize_localization_result
from services.localizer.registry import DEFAULT_ALGORITHM_REGISTRY, LocalizationInput
from services.localizer.verify_projection import query_local_coordinate_transform

CST = ZoneInfo("Asia/Shanghai")

router = APIRouter(prefix="/api/localize", tags=["localize"])

ALGORITHMS = {
    algorithm_id: {
        "feature": DEFAULT_ALGORITHM_REGISTRY.get(algorithm_id).feature_method,
        "label": DEFAULT_ALGORITHM_REGISTRY.get(algorithm_id).label,
    }
    for algorithm_id in DEFAULT_ALGORITHM_REGISTRY.ids()
}
_queued_task_ids: set[int] = set()
_queue_lock = threading.Lock()


def _parse_k(k):
    """解析 camera_intrinsics 为 numpy 3×3。"""
    if k is None:
        return None
    try:
        import numpy as np
        return np.asarray(k, dtype=np.float64).reshape(3, 3)
    except Exception:
        return None

def log(msg: str):
    print(f"[LOCALIZE] {msg}")


class LocalizeRequest(BaseModel):
    image_id: int
    algorithms: list[str] = Field(default_factory=lambda: ["salad_roma"])
    # 兼容旧页面/API；新调用请传 algorithms。
    feature_methods: Optional[list[str]] = None
    match_methods: Optional[list[str]] = None
    max_iterations: int = 2
    debug_visualizations: bool = False
    # v2 扩展参数
    camera_intrinsics: Optional[list] = None   # 3×3 矩阵（行优先）
    fov_deg: float = 75.0
    use_pose_prior: bool = False
    prior_position: Optional[list] = None      # [x, y, z]
    prior_radius: float = 15.0
    reproj_error: float = 4.0
    min_inliers: int = 12
    geometric_verify: bool = False  # tile↔query 不满足对极几何，默认关
    keep_aspect_ratio: bool = True
    coordinate_threshold_m: float = Field(default=0.3, gt=0.0)

    @field_validator("camera_intrinsics")
    @classmethod
    def validate_camera_intrinsics(cls, value):
        if value is None:
            return value
        array = np.asarray(value)
        if array.shape not in {(3, 3), (9,)}:
            raise ValueError("camera_intrinsics must be a 3x3 matrix or a flat array of length 9")
        return value

    @field_validator("prior_position")
    @classmethod
    def validate_prior_position(cls, value):
        if value is not None and len(value) != 3:
            raise ValueError("prior_position must contain x, y and z")
        return value


class LocalizeResult(BaseModel):
    feature_method: str
    match_method: str
    success: bool
    pose: Optional[dict] = None
    inliers: int = 0
    comparison_image: Optional[str] = None
    reprojection_image: Optional[str] = None
    matched_points: list = Field(default_factory=list)


class CoordinateTransformRequest(BaseModel):
    """查询图选点到当前定位结果本地坐标产物的稳定请求。"""

    task_id: int
    result_index: int = Field(default=0, ge=0)
    u: float = Field(ge=0.0, le=1.0)
    v: float = Field(ge=0.0, le=1.0)


def _public_artifact_path(path):
    if not path:
        return path
    value = str(path).replace("\\", "/")
    marker = "/projections/"
    if marker in value:
        value = value.split(marker, 1)[1]
    elif value.startswith("projections/"):
        value = value[len("projections/"):]
    value = value.lstrip("/")
    return f"/projections/{value}"


def _algorithm_id_to_matcher_type(algorithm_id: str) -> str:
    """根据初始定位算法 ID 推导精化步骤应使用的匹配器类型。

    BUG-003-05: 精化必须复用初始定位的匹配器，不能固定 LightGlue。
    """
    _MATCHER_MAP = {
        "salad_roma": "tiny_roma",       # SALAD+RoMa → TinyRoMa
        "salad_roma_v2": "lightglue",    # SALAD v2 → DISK+LightGlue
        "salad_roma_v2_loftr": "lightglue",  # LoFTR 路径暂无 refine 匹配器，回退 LightGlue
        "hybrid": "lightglue",           # Hybrid 路径回退 LightGlue
        "salad_lightglue": "lightglue",
    }
    return _MATCHER_MAP.get(algorithm_id, "lightglue")


def _append_result(results, algorithm_id, result, *, min_inliers, elapsed_s=None):
    """统一格式化并保留算法诊断信息。"""
    definition = DEFAULT_ALGORITHM_REGISTRY.get(algorithm_id)
    normalized = normalize_localization_result(
        algorithm_id,
        result,
        min_inliers=min_inliers,
        elapsed_s=elapsed_s,
        feature_method=definition.feature_method,
    )
    for key in ("query_image", "comparison_image", "reprojection_image"):
        public_path = _public_artifact_path(normalized.get(key))
        normalized[key] = public_path
        if public_path:
            normalized["artifacts"][key] = public_path
    results.append(normalized)


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
    task = None
    try:
        task = db.query(TaskModel).filter(TaskModel.id == task_id).first()
        if not task:
            return
        request = task.request_json or {}
        algorithms = request.get("algorithms", ["salad_roma"])
        max_iterations = max(1, min(int(request.get("max_iterations", 2)), 10))
        debug_visualizations = bool(request.get("debug_visualizations", False))
        # v2 扩展参数
        camera_intrinsics = request.get("camera_intrinsics")
        fov_deg = float(request.get("fov_deg", 75.0))
        use_pose_prior = bool(request.get("use_pose_prior", False))
        prior_position = request.get("prior_position")
        prior_radius = float(request.get("prior_radius", 15.0))
        reproj_error = float(request.get("reproj_error", 4.0))
        min_inliers = int(request.get("min_inliers", 12))
        geometric_verify = bool(request.get("geometric_verify", False))
        keep_aspect_ratio = bool(request.get("keep_aspect_ratio", True))
        coordinate_threshold_m = float(request.get("coordinate_threshold_m", 0.3))
        task.status = "running"
        task.result_json = {"results": [], "total": len(algorithms)}
        db.commit()

        from services.localizer import load_colmap

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
            definition = DEFAULT_ALGORITHM_REGISTRY.get(algorithm)
            log(f"[task={task_id} algorithm={algorithm}] 开始: {definition.label}")
            started_at = time.perf_counter()
            try:
                localization_input = LocalizationInput(
                    image_path=img.path,
                    output_dir=out_dir,
                    max_iterations=max_iterations,
                    debug_visualizations=debug_visualizations,
                    camera_intrinsics=_parse_k(camera_intrinsics),
                    fov_deg=fov_deg,
                    use_pose_prior=use_pose_prior,
                    prior_position=tuple(prior_position) if prior_position else None,
                    prior_radius=prior_radius,
                    reproj_error=reproj_error,
                    min_inliers=min_inliers,
                    geometric_verify=geometric_verify,
                    keep_aspect_ratio=keep_aspect_ratio,
                    coordinate_threshold_m=coordinate_threshold_m,
                )
                result = DEFAULT_ALGORITHM_REGISTRY.run(algorithm, localization_input)
            except Exception as e:
                log(f"[task={task_id} algorithm={algorithm}] 异常: {e}")
                result = {
                    "success": False,
                    "error": {"code": "algorithm_exception", "message": str(e)},
                }
            _append_result(
                results,
                algorithm,
                result,
                min_inliers=min_inliers,
                elapsed_s=time.perf_counter() - started_at,
            )
            task.result_json = {"results": results, "total": len(algorithms)}
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(task, "result_json")
            db.commit()

        task.result_json = {"results": results, "total": len(algorithms)}
        task.status = "completed"
        task.finished_at = datetime.datetime.now(CST)
        db.commit()
    except Exception as e:
        if task is not None:
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
            "camera_intrinsics": req.camera_intrinsics,
            "fov_deg": req.fov_deg,
            "use_pose_prior": req.use_pose_prior,
            "prior_position": req.prior_position,
            "prior_radius": req.prior_radius,
            "reproj_error": req.reproj_error,
            "min_inliers": req.min_inliers,
            "geometric_verify": req.geometric_verify,
            "keep_aspect_ratio": req.keep_aspect_ratio,
            "coordinate_threshold_m": req.coordinate_threshold_m,
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

    if task.status in {"pending", "running"}:
        current = task.result_json or {}
        return {
            "status": "running",
            "results": current.get("results", []),
            "total": current.get("total", 0),
            "image_id": task.image_id,
        }
    if task.status == "failed":
        return {"status": "failed", "results": [], "error": task.error_message}

    results = []
    if task.result_json and "results" in task.result_json:
        results = task.result_json["results"]

    return {"status": "completed", "results": results, "image_id": task.image_id}


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
    import cv2
    import numpy as np
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
    cm[0, 0] *= scale
    cm[1, 1] *= scale
    cm[0, 2] *= scale
    cm[1, 2] *= scale

    q = pose["quaternion"]
    t = pose["translation"]
    # quaternion -> rvec
    from scipy.spatial.transform import Rotation
    r = Rotation.from_quat([q[1], q[2], q[3], q[0]])  # xyzw -> wxyz
    rvec = cv2.Rodrigues(r.as_matrix())[0]
    tvec = np.array(t, dtype=np.float64).reshape(3, 1)

    out_dir = f"projections/localize/task_{task.id}"

    # 根据初始定位算法推导匹配器（BUG-003-05: 复用初始匹配器而非固定 LightGlue）
    algorithm_id = target.get("match_method", target.get("algorithm_id", ""))
    matcher_type = _algorithm_id_to_matcher_type(algorithm_id)

    refine_result = refine_pose_with_roma(
        img.path, rvec, tvec, cm, q_w, q_h,
        all_pts, all_col, out_dir=out_dir,
        matcher_type=matcher_type,
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
    except Exception:
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


@router.post("/verify-report")
def generate_verify_report(request: dict, db: Session = Depends(get_db)):
    """生成内部投影一致性 HTML 报告。"""
    image_id = request.get("image_id")
    if not image_id:
        raise HTTPException(422, "image_id required")

    img = db.query(ImageModel).filter(ImageModel.id == image_id).first()
    if not img or not os.path.exists(img.path):
        raise HTTPException(404, "Image not found")

    try:
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        stem = Path(img.filename).stem[:32]
        run_id = datetime.datetime.now(CST).strftime("%Y%m%dT%H%M%S%f")
        relative_report = Path("reports/generated") / f"verify_{stem}_{run_id}.html"
        result = subprocess.run(
            [
                sys.executable,
                "scripts/generate_verify_report.py",
                "--image",
                os.path.abspath(img.path),
                "--top-k",
                "3",
                "--output",
                str(relative_report),
            ],
            capture_output=True,
            text=True,
            cwd=repo_root,
            timeout=120,
        )
        if result.returncode == 0:
            return {
                "success": True,
                "report_path": relative_report.as_posix(),
                "metric_type": "projection_consistency",
                "absolute_accuracy": "not_available",
            }
        detail = (result.stderr or result.stdout or "report generation failed")[:500]
        raise HTTPException(500, detail)
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(504, "verification report generation timed out") from exc
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e)) from e


@router.post("/verify-e2e")
def verify_e2e(db: Session = Depends(get_db)):
    """运行端到端回归测试，返回结构化结果（无需前端手动点 query）。

    用于 CI/CD 门禁和前端"验证"按钮调用。
    """
    import subprocess, json
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest",
             "services/tests/test_e2e_ground_filtering.py",
             "-v", "--tb=short", "-q"],
            capture_output=True, text=True, timeout=120,
            cwd=str(Path(__file__).resolve().parent.parent.parent),
        )
        return {
            "success": r.returncode == 0,
            "returncode": r.returncode,
            "stdout": r.stdout[-3000:],
            "stderr": r.stderr[-1000:],
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "测试超时（>120s）"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/coordinate-transform")
def coordinate_transform(
    request: CoordinateTransformRequest,
    db: Session = Depends(get_db),
):
    """在任务自产的单应矩阵和最终投影 NPY 上执行坐标转换。"""
    task = db.query(TaskModel).filter(TaskModel.id == request.task_id).first()
    if not task:
        raise HTTPException(404, "Localization task not found")

    results = (task.result_json or {}).get("results", [])
    if request.result_index >= len(results):
        raise HTTPException(404, "Localization result not found")
    context = results[request.result_index].get("coordinate_transform")
    if not context or context.get("status") != "ready":
        raise HTTPException(
            409,
            "该定位结果没有本地坐标转换产物，请重新定位后再选点",
        )
    return query_local_coordinate_transform(context, u=request.u, v=request.v)


@router.post("/localize/ace")
async def ace_localize_endpoint(image_id: int = Form(...), db: Session = Depends(get_db)):
    """ACE 单次定位（场景坐标回归 + PnP，无需多轮迭代）"""
    from services.localizer.ace_trainer import ace_localize
    from services.localizer.coord_regression import CoordRegression
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
