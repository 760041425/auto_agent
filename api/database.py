from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase

import os
SQLALCHEMY_DATABASE_URL = f"sqlite:///{os.path.abspath('./query_images/app.db')}"

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def migrate_schema():
    """为已有 SQLite 库补充可恢复任务所需字段。"""
    with engine.begin() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(tasks)"))}
        if "task_type" not in columns:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN task_type VARCHAR NOT NULL DEFAULT 'compare'"))
        if "request_json" not in columns:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN request_json JSON"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
