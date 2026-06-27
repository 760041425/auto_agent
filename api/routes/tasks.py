import datetime
import threading

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import ImageModel, TaskModel, ReportModel
from api.schemas import TaskCreate, TaskResponse, ReportResponse

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def run_comparison_task(task_id: int, image_id: int, db_url: str):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=engine)

    db = Session()
    try:
        task = db.query(TaskModel).filter(TaskModel.id == task_id).first()
        if not task:
            return
        task.status = "processing"
        db.commit()

        img = db.query(ImageModel).filter(ImageModel.id == image_id).first()
        if not img:
            task.status = "failed"
            task.error_message = "Image not found"
            db.commit()
            return

        from services.matcher import compute_image_area_3d
        result = compute_image_area_3d(img.path)

        task.result_json = result
        task.status = "completed"
        task.finished_at = datetime.datetime.utcnow()

        report = ReportModel(
            task_id=task.id,
            matched=1 if result.get("matched") else 0,
            total_matches=result.get("total_matches", 0),
            center_3d_x=result["center_3d"][0] if result.get("center_3d") else None,
            center_3d_y=result["center_3d"][1] if result.get("center_3d") else None,
            center_3d_z=result["center_3d"][2] if result.get("center_3d") else None,
            regions_json=result.get("regions"),
            confidence=min(result.get("total_matches", 0) / 100, 1.0),
        )
        db.add(report)
        db.commit()
    except Exception as e:
        task.status = "failed"
        task.error_message = str(e)
        db.commit()
    finally:
        db.close()


@router.post("/compare", response_model=TaskResponse)
def create_comparison_task(req: TaskCreate, db: Session = Depends(get_db)):
    img = db.query(ImageModel).filter(ImageModel.id == req.image_id).first()
    if not img:
        raise HTTPException(404, "Image not found")

    task = TaskModel(image_id=req.image_id, status="pending")
    db.add(task)
    db.commit()
    db.refresh(task)

    threading.Thread(
        target=run_comparison_task,
        args=(task.id, req.image_id, "sqlite:///./projections/app.db"),
        daemon=True,
    ).start()

    return task


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(TaskModel).filter(TaskModel.id == task_id).first()
    if not task:
        raise HTTPException(404, "Task not found")
    return task


@router.get("", response_model=list[TaskResponse])
def list_tasks(skip: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    return db.query(TaskModel).order_by(TaskModel.created_at.desc()).offset(skip).limit(limit).all()


@router.get("/{task_id}/report")
def get_report(task_id: int, db: Session = Depends(get_db)):
    report = db.query(ReportModel).filter(ReportModel.task_id == task_id).first()
    if not report:
        raise HTTPException(404, "Report not found")
    task = report.task
    verification = None
    matched_points = None
    all_matched_points = None
    if task and task.result_json:
        result = task.result_json
        verification = result.get("verification")
        matched_points = result.get("matched_points")
        all_matched_points = result.get("all_matched_points")
    return {
        "id": report.id,
        "task_id": report.task_id,
        "matched": bool(report.matched),
        "total_matches": report.total_matches,
        "center_3d": {
            "x": report.center_3d_x,
            "y": report.center_3d_y,
            "z": report.center_3d_z,
        } if report.center_3d_x is not None else None,
        "regions": report.regions_json,
        "confidence": report.confidence,
        "verification": verification,
        "matched_points": matched_points,
        "all_matched_points": all_matched_points,
        "created_at": report.created_at,
    }
