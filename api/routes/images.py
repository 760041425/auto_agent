import os
import uuid
import shutil
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import ImageModel
from api.schemas import ImageUploadResponse, ImageListResponse

router = APIRouter(prefix="/api/images", tags=["images"])

UPLOAD_DIR = Path("query_images")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@router.post("/upload", response_model=ImageUploadResponse)
async def upload_image(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "Only image files are allowed")

    ext = Path(file.filename).suffix or ".jpg"
    stored_name = f"{uuid.uuid4()}{ext}"
    file_path = UPLOAD_DIR / stored_name

    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    from PIL import Image
    try:
        img = Image.open(file_path)
        w, h = img.size
    except Exception:
        w, h = None, None

    record = ImageModel(
        filename=stored_name,
        original_name=file.filename or stored_name,
        path=str(file_path),
        status="uploaded",
        width=w,
        height=h,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.get("", response_model=list[ImageListResponse])
def list_images(skip: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    return db.query(ImageModel).order_by(ImageModel.created_at.desc()).offset(skip).limit(limit).all()


@router.get("/{image_id}", response_model=ImageListResponse)
def get_image(image_id: int, db: Session = Depends(get_db)):
    img = db.query(ImageModel).filter(ImageModel.id == image_id).first()
    if not img:
        raise HTTPException(404, "Image not found")
    return img


@router.delete("/{image_id}")
def delete_image(image_id: int, db: Session = Depends(get_db)):
    img = db.query(ImageModel).filter(ImageModel.id == image_id).first()
    if not img:
        raise HTTPException(404, "Image not found")
    if os.path.exists(img.path):
        os.remove(img.path)
    db.delete(img)
    db.commit()
    return {"deleted": True}
