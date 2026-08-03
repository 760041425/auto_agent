import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from api.database import engine, Base, migrate_schema
from api.routes import images, tasks, preprocess, localize
from api.runtime import ensure_runtime_directories
from services.localizer.logger_config import get_http_logger, configure_uvicorn_access_logger


PROJECT_ROOT = Path(__file__).resolve().parent.parent
QUERY_IMAGES_DIR, PROJECTIONS_DIR, REPORTS_DIR, LOGS_DIR = ensure_runtime_directories(PROJECT_ROOT)
_http_logger = get_http_logger("api.http")
configure_uvicorn_access_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    migrate_schema()
    # 进程异常退出时留下的任务会在本次启动后重新进入队列。
    from api.routes.localize import resume_pending_localize_tasks
    resume_pending_localize_tasks()
    _http_logger.info("Server started")
    yield


app = FastAPI(title="LAS 3D Query API", lifespan=lifespan)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """记录每个 HTTP 请求到 http_api.log。"""
    response = await call_next(request)
    _http_logger.info(f"{request.method} {request.url.path} → {response.status_code}")
    return response


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
app.mount("/images", StaticFiles(directory=str(QUERY_IMAGES_DIR)), name="query_images")
app.mount("/projections", StaticFiles(directory=str(PROJECTIONS_DIR)), name="projections")
app.mount("/reports", StaticFiles(directory=str(REPORTS_DIR)), name="reports")
app.mount("/", StaticFiles(directory=str(PROJECT_ROOT / "web"), html=True), name="static")
