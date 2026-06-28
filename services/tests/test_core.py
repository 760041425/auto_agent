import json
from pathlib import Path

import pytest

from services.las_processor.colmap_reader import read_images_txt, read_points3d_txt
from services.las_processor.projection import project_las_multi_view


def test_read_images_txt():
    imgs = read_images_txt("las/images.txt")
    assert len(imgs) > 0
    assert imgs[0].image_id > 0
    assert len(imgs[0].points2d) > 0
    assert imgs[0].name


def test_read_points3d_txt():
    pts = read_points3d_txt("las/points3D.txt")
    assert len(pts) > 0
    first = list(pts.values())[0]
    assert first.x != 0


def test_las_projection():
    las_files = [f for f in sorted(Path("las").glob("*.las")) if "subsample" not in f.name]
    las_path = str(las_files[0]) if las_files else "las/default_2026-05-28-112428.las"
    result = project_las_multi_view(
        las_path,
        "projections/test_proj",
    )
    assert len(result) > 0
    first = result[0]
    assert first["width"] > 0
    assert first["height"] > 0
    assert Path(first["image_path"]).exists()
    assert Path(first["coord_map_path"]).exists()

    with open(first["coord_map_path"]) as f:
        data = json.load(f)
    assert len(data["pixels"]) > 0

    import shutil
    shutil.rmtree("projections/test_proj", ignore_errors=True)


def test_api_health():
    import socket
    import requests
    import uvicorn
    import threading
    import time

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    def run():
        uvicorn.run("api.main:app", host="127.0.0.1", port=port, log_level="error")

    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(3)

    base = f"http://127.0.0.1:{port}"
    for _ in range(5):
        try:
            r = requests.get(f"{base}/api/health", timeout=2)
            if r.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(1)

    r = requests.get(f"{base}/api/health")
    assert r.status_code == 200

    r = requests.get(f"{base}/api/images")
    assert r.status_code == 200

    r = requests.get(f"{base}/")
    assert r.status_code == 200
