#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
PID_FILE="$ROOT_DIR/.uvicorn.pid"
LOG_FILE="$ROOT_DIR/logs/uvicorn.log"
APP_MODULE="api.main:app"

# macOS OpenMP 兼容：torch+faiss+scipy+PIL 共存时 libomp.dylib 重复初始化 → SIGABRT
export KMP_DUPLICATE_LIB_OK="${KMP_DUPLICATE_LIB_OK:-TRUE}"

mkdir -p "$ROOT_DIR/logs"

start_server() {
  if [[ -f "$PID_FILE" ]]; then
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      echo "Server is already running with PID $pid"
      return 0
    fi
    rm -f "$PID_FILE"
  fi

  nohup "$PYTHON_BIN" -m uvicorn "$APP_MODULE" --host "$HOST" --port "$PORT" >"$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"

  sleep 2
  pid="$(cat "$PID_FILE")"
  if kill -0 "$pid" 2>/dev/null; then
    echo "Server started (PID $pid)"
    echo "Log: $LOG_FILE"
  else
    echo "Server failed to start. Check $LOG_FILE"
    exit 1
  fi
}

stop_server() {
  local pid=""
  
  if [[ -f "$PID_FILE" ]]; then
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid"
      rm -f "$PID_FILE"
      echo "Server stopped (PID $pid)"
      return 0
    else
      rm -f "$PID_FILE"
      echo "Stale PID file removed"
    fi
  fi

  pid="$(lsof -ti :"$PORT" -sTCP:LISTEN 2>/dev/null | head -n 1 || true)"
  if [[ -n "$pid" ]]; then
    kill "$pid" 2>/dev/null || kill -9 "$pid" 2>/dev/null
    echo "Server stopped by port $PORT (PID $pid)"
    return 0
  fi

  pid="$(pgrep -f "uvicorn.*$APP_MODULE" 2>/dev/null | head -n 1 || true)"
  if [[ -n "$pid" ]]; then
    kill "$pid" 2>/dev/null || kill -9 "$pid" 2>/dev/null
    echo "Server stopped by process name (PID $pid)"
    return 0
  fi

  pid="$(pgrep -f "python.*uvicorn" 2>/dev/null | head -n 1 || true)"
  if [[ -n "$pid" ]]; then
    kill "$pid" 2>/dev/null || kill -9 "$pid" 2>/dev/null
    echo "Server stopped by python process (PID $pid)"
    return 0
  fi

  echo "Server is not running"
}

restart_server() {
  stop_server
  start_server
}

status_server() {
  if [[ -f "$PID_FILE" ]]; then
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      echo "Server is running (PID $pid)"
    else
      echo "Server is not running"
    fi
  else
    echo "Server is not running"
  fi
}

case "${1:-start}" in
  start)
    start_server
    ;;
  stop)
    stop_server
    ;;
  restart)
    restart_server
    ;;
  status)
    status_server
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|status}"
    exit 1
    ;;
esac
