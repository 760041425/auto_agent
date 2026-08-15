"""应用运行目录约定。"""

from __future__ import annotations

from pathlib import Path


RUNTIME_DIRECTORY_NAMES = ("query_images", "projections", "reports", "logs")


def ensure_runtime_directories(root: str | Path) -> tuple[Path, ...]:
    root_path = Path(root)
    paths = tuple(root_path / name for name in RUNTIME_DIRECTORY_NAMES)
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
    return paths
