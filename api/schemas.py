from datetime import datetime

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
    width: int | None = None
    height: int | None = None
    created_at: datetime


class TaskCreate(BaseModel):
    image_id: int


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    image_id: int
    status: str
    result_json: dict | None = None
    error_message: str | None = None
    created_at: datetime
    finished_at: datetime | None = None


class ReportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    task_id: int
    matched: bool
    total_matches: int
    center_3d: dict | None = None
    regions: list | None = None
    confidence: float | None = None
    created_at: datetime


class MatchRegion(BaseModel):
    x: float
    y: float
    width: float
    height: float


class RegionQueryRequest(BaseModel):
    region: MatchRegion | None = None
