import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import Column, Integer, String, Float, Text, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship

from api.database import Base

CST = ZoneInfo("Asia/Shanghai")


def _cst_now():
    return datetime.datetime.now(CST)


class ImageModel(Base):
    __tablename__ = "images"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, nullable=False)
    original_name = Column(String, nullable=False)
    path = Column(String, nullable=False)
    status = Column(String, default="uploaded")
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=_cst_now)

    tasks = relationship("TaskModel", back_populates="image")


class TaskModel(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    image_id = Column(Integer, ForeignKey("images.id"), nullable=False)
    status = Column(String, default="pending")
    # 任务类型和请求参数必须持久化；进程重启后可安全恢复 pending 任务。
    task_type = Column(String, default="compare", nullable=False)
    request_json = Column(JSON, nullable=True)
    result_json = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_cst_now)
    finished_at = Column(DateTime, nullable=True)

    image = relationship("ImageModel", back_populates="tasks")
    report = relationship("ReportModel", back_populates="task", uselist=False)


class ReportModel(Base):
    __tablename__ = "reports"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False, unique=True)
    matched = Column(Integer, default=0)
    total_matches = Column(Integer, default=0)
    center_3d_x = Column(Float, nullable=True)
    center_3d_y = Column(Float, nullable=True)
    center_3d_z = Column(Float, nullable=True)
    regions_json = Column(JSON, nullable=True)
    confidence = Column(Float, nullable=True)
    verification_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=_cst_now)

    task = relationship("TaskModel", back_populates="report")
