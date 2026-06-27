import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from api.database import engine, Base
from api.routes import images, tasks, preprocess


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
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
app.mount("/images", StaticFiles(directory="query_images"), name="query_images")
app.mount("/projections", StaticFiles(directory="projections"), name="projections")
app.mount("/", StaticFiles(directory="web", html=True), name="static")
