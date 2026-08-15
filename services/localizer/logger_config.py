"""
日志配置 — 分离 HTTP API 日志和业务日志

- HTTP API 日志 → logs/http_api.log（FastAPI/uvicorn 请求）
- 业务日志 → logs/backend.log（localizer, matcher, salad_roma 等）
"""

import logging
from pathlib import Path

LOG_DIR = Path(__file__).parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

# ── 格式 ──────────────────────────────────────────────
_FMT = logging.Formatter(
    "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_FMT_SHORT = logging.Formatter(
    "%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)

# ── HTTP API 日志 ──────────────────────────────────────
HTTP_LOG_PATH = LOG_DIR / "http_api.log"
_http_fh = logging.FileHandler(str(HTTP_LOG_PATH), mode="a", encoding="utf-8")
_http_fh.setFormatter(_FMT)
_http_sh = logging.StreamHandler()
_http_sh.setFormatter(_FMT_SHORT)


def get_http_logger(name: str = "http_api") -> logging.Logger:
    """HTTP API 请求日志（FastAPI 路由级别）。"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        logger.addHandler(_http_fh)
        logger.addHandler(_http_sh)
        logger.propagate = False
    return logger


def configure_uvicorn_access_logger() -> logging.Logger:
    """让 uvicorn 访问日志写入 HTTP 文件，重复调用不增加 handler。"""
    logger = logging.getLogger("uvicorn.access")
    if _http_fh not in logger.handlers:
        logger.addHandler(_http_fh)
    logger.propagate = False
    return logger


# ── 业务日志 ───────────────────────────────────────────
BACKEND_LOG_PATH = LOG_DIR / "backend.log"
_backend_fh = logging.FileHandler(str(BACKEND_LOG_PATH), mode="a", encoding="utf-8")
_backend_fh.setFormatter(_FMT)
_backend_sh = logging.StreamHandler()
_backend_sh.setFormatter(_FMT_SHORT)


def get_backend_logger(name: str = "backend") -> logging.Logger:
    """业务逻辑日志（localizer, matcher, preprocess 等）。"""
    logger = logging.getLogger(f"backend.{name}")
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        logger.addHandler(_backend_fh)
        logger.addHandler(_backend_sh)
        logger.propagate = False
    return logger


# ── 迁移旧 logger ──────────────────────────────────────
def migrate_logger(old_name: str, new_name: str) -> logging.Logger:
    """把旧 logger 的 handler 替换为新 backend logger。"""
    old = logging.getLogger(old_name)
    # 清除旧 handler
    for h in old.handlers[:]:
        old.removeHandler(h)
    # 使用新 backend handler
    if not old.handlers:
        old.addHandler(_backend_fh)
        old.addHandler(_backend_sh)
        old.propagate = False
    return old
