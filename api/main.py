import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from api.database import engine, Base, migrate_schema
from api.routes import images, tasks, preprocess, localize


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    migrate_schema()
    # 进程异常退出时留下的任务会在本次启动后重新进入队列。
    from api.routes.localize import resume_pending_localize_tasks
    resume_pending_localize_tasks()
    yield


app = FastAPI(title="LAS 3D Query API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/health")
def health():
    return {"status": "ok"}


app.include_router(images.router)
app.include_router(tasks.router)
app.include_router(preprocess.router)
app.include_router(localize.router)
app.mount("/images", StaticFiles(directory="query_images"), name="query_images")
app.mount("/projections", StaticFiles(directory="projections"), name="projections")
app.mount("/", StaticFiles(directory="web", html=True), name="static")
