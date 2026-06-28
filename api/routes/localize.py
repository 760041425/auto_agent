import datetime
import threading
import os
import json
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import TaskModel

CST = ZoneInfo("Asia/Shanghai")

router = APIRouter(prefix="/api/localize", tags=["localize"])


class LocalizeRequest(BaseModel):
    image_id: int
    feature_methods: list[str] = ["sift"]
    match_methods: list[str] = ["flann", "bf", "flann_lowes", "bf_cross", "knn_rank"]


class LocalizeResult(BaseModel):
    feature_method: str
    match_method: str
    success: bool
    pose: Optional[dict] = None
    inliers: int = 0
    comparison_image: Optional[str] = None
    reprojection_image: Optional[str] = None
    matched_points: list = []


def run_localize_task(task_id: int, image_id: int, feature_methods: list[str], match_methods: list[str], db_url: str):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        task = db.query(TaskModel).filter(TaskModel.id == task_id).first()
        if not task:
            return
        task.status = "running"
        db.commit()

        from services.localizer import load_colmap, localize_image

        load_colmap()

        from api.models import ImageModel
        img = db.query(ImageModel).filter(ImageModel.id == image_id).first()
        if not img:
            task.status = "failed"
            task.error_message = "Image not found"
            db.commit()
            return

        results = []
        for fm in feature_methods:
            for mm in match_methods:
                try:
                    out_dir = f"projections/localize/task_{task_id}"
                    result = localize_image(
                        img.path,
                        output_dir=out_dir,
                        feature_method=fm,
                        match_method=mm,
                        max_iterations=3,
                    )
                    comparison_path = None
                    reprojection_path = None
                    if result.get("comparison_image"):
                        comparison_path = f"/projections/localize/task_{task_id}/comparison_{fm}_{mm}.png"
                        src = result["comparison_image"]
                        if src:
                            dst = Path(out_dir) / f"comparison_{fm}_{mm}.png"
                            if Path(src).exists():
                                import shutil
                                shutil.copy2(src, str(dst))
                    if result.get("reprojection_image"):
                        reprojection_path = f"/projections/localize/task_{task_id}/reprojection_{fm}_{mm}.png"
                        src = result["reprojection_image"]
                        if src:
                            dst = Path(out_dir) / f"reprojection_{fm}_{mm}.png"
                            if Path(src).exists():
                                import shutil
                                _dst = Path(out_dir)
                                _dst.mkdir(parents=True, exist_ok=True)
                                shutil.copy2(src, str(dst))

                    results.append({
                        "feature_method": fm,
                        "match_method": mm,
                        "success": result.get("success", False),
                        "pose": result.get("pose"),
                        "inliers": result.get("inliers", 0),
                        "comparison_image": comparison_path,
                        "reprojection_image": reprojection_path,
                        "matched_points": result.get("matched_points", []),
                    })
                except Exception as e:
                    results.append({
                        "feature_method": fm,
                        "match_method": mm,
                        "success": False,
                        "pose": None,
                        "inliers": 0,
                        "comparison_image": None,
                        "reprojection_image": None,
                        "matched_points": [],
                        "error": str(e),
                    })

        task.result_json = {"results": results}
        task.status = "completed"
        task.finished_at = datetime.datetime.now(CST)
        db.commit()
    except Exception as e:
        task.status = "failed"
        task.error_message = str(e)
        db.commit()
    finally:
        db.close()


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
    task = TaskModel(image_id=req.image_id, status="pending")
    db.add(task)
    db.commit()
    db.refresh(task)

    threading.Thread(
        target=run_localize_task,
        args=(task.id, req.image_id, req.feature_methods, req.match_methods, f"sqlite:///{os.path.abspath('projections/app.db')}"),
        daemon=True,
    ).start()

    return {"task_id": task.id}


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
