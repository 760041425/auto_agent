from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class ImageUploadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    filename: str
    original_name: str
    status: str
    created_at: datetime


class ImageListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    filename: str
    original_name: str
    status: str
    width: Optional[int] = None
    height: Optional[int] = None
    created_at: datetime


class TaskCreate(BaseModel):
    image_id: int


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    image_id: int
    status: str
    result_json: Optional[dict] = None
    error_message: Optional[str] = None
    created_at: datetime
    finished_at: Optional[datetime] = None


class ReportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    task_id: int
    matched: bool
    total_matches: int
    center_3d: Optional[dict] = None
    regions: Optional[list] = None
    confidence: Optional[float] = None
    created_at: datetime


class MatchRegion(BaseModel):
    x: float
    y: float
    width: float
    height: float


class RegionQueryRequest(BaseModel):
    region: Optional[MatchRegion] = None
