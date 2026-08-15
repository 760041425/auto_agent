#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
PID_FILE="$ROOT_DIR/.uvicorn.pid"
LOG_FILE="$ROOT_DIR/logs/uvicorn.log"

# 颜色
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

_print_status() {
  local name="$1"   # 服务名
  local status="$2" # ok / fail / warn
  local info="$3"   # 附加信息
  case "$status" in
    ok)   echo -e "  [${GREEN}✓${NC}] $name — $info" ;;
    fail) echo -e "  [${RED}✗${NC}] $name — $info" ;;
    warn) echo -e "  [${YELLOW}⚠${NC}] $name — $info" ;;
  esac
}

_check_pid() {
  local pid="$1"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

_http_ok() {
  curl -sf "http://localhost:$PORT/api/health" >/dev/null 2>&1
}

# ── 状态检测 ──────────────────────────────────
check_all() {
  local all_ok=true
  local pid=""

  echo "=== LAS 3D Query 服务状态 ==="
  echo ""

  # 1. PID 文件检查
  if [[ -f "$PID_FILE" ]]; then
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if _check_pid "$pid"; then
      _print_status "uvicorn 进程" ok "PID $pid"
    else
      _print_status "uvicorn 进程" fail "PID 文件存在但进程已死 (stale PID $pid)"
      all_ok=false
    fi
  else
    _print_status "uvicorn 进程" fail "PID 文件不存在 (服务未启动)"
    all_ok=false
  fi

  # 2. HTTP 健康检查
  if _http_ok; then
    _print_status "HTTP /api/health" ok "http://localhost:$PORT/api/health"
  else
    _print_status "HTTP /api/health" fail "无法访问 health 端点"
    all_ok=false
  fi

  # 3. 端口监听
  local port_pid
  port_pid="$(lsof -ti :"$PORT" -sTCP:LISTEN 2>/dev/null | head -n 1 || true)"
  if [[ -n "$port_pid" ]]; then
    _print_status "端口 $PORT" ok "被 PID $port_pid 监听"
  else
    _print_status "端口 $PORT" fail "无进程监听"
    all_ok=false
  fi

  # 4. 日志状态
  if [[ -f "$LOG_FILE" ]]; then
    local log_size
    log_size="$(wc -c < "$LOG_FILE" 2>/dev/null || echo 0)"
    local log_mtime
    log_mtime="$(stat -f "%Sm" -t "%Y-%m-%d %H:%M" "$LOG_FILE" 2>/dev/null || echo '?')"
    _print_status "日志文件" ok "${log_size} bytes, 最后更新 $log_mtime"
  else
    _print_status "日志文件" warn "日志文件不存在"
  fi

  echo ""

  if $all_ok; then
    echo "  整体状态: ${GREEN}正常${NC}"
    echo "  服务地址: http://localhost:$PORT"
    echo "  API 文档: http://localhost:$PORT/docs"
    return 0
  else
    echo "  整体状态: ${RED}异常${NC}"
    return 1
  fi
}

# ── 自动修复 ──────────────────────────────────
auto_fix() {
  echo "=== 自动修复 ==="

  # 清理 stale PID
  if [[ -f "$PID_FILE" ]]; then
    local pid
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$PID_FILE"
      echo "  清理了 stale PID 文件"
    fi
  fi

  # 如果端口被占用但不是我们的进程，先杀掉
  local port_pid
  port_pid="$(lsof -ti :"$PORT" -sTCP:LISTEN 2>/dev/null | head -n 1 || true)"
  if [[ -n "$port_pid" ]]; then
    local saved_pid
    saved_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ "$port_pid" != "$saved_pid" ]]; then
      echo "  端口 $PORT 被未知进程 PID $port_pid 占用，尝试停止..."
      kill "$port_pid" 2>/dev/null || kill -9 "$port_pid" 2>/dev/null || true
      sleep 1
    fi
  fi

  echo "  正在重新启动服务..."
  "$ROOT_DIR/scripts/server.sh" start

  echo -n "  等待就绪"
  for i in $(seq 1 15); do
    if curl -sf "http://localhost:$PORT/api/health" >/dev/null 2>&1; then
      echo ""
      echo "  ${GREEN}✓ 服务已恢复${NC}"
      return 0
    fi
    echo -n "."
    sleep 1
  done
  echo ""
  echo "  ${RED}✗ 启动超时，请检查日志: $LOG_FILE${NC}"
  return 1
}

# ── 主逻辑 ──────────────────────────────────
case "${1:-status}" in
  status)
    check_all
    ;;
  check)
    # 静默检查，适合 cron / CI，仅返回 exit code
    if _http_ok && [[ -f "$PID_FILE" ]] && _check_pid "$(cat "$PID_FILE")"; then
      exit 0
    else
      exit 1
    fi
    ;;
  fix|repair|restart)
    # 检测异常后自动修复
    if check_all; then
      echo "所有服务正常，无需修复。"
      exit 0
    fi
    auto_fix
    ;;
  daemon)
    # 持续检测模式（每隔 N 秒检测一次，异常时自动修复）
    interval="${2:-60}"
    echo "=== 守护模式启动 (检测间隔: ${interval}s) ==="
    echo "按 Ctrl+C 停止"
    echo ""
    while true; do
      if ! _http_ok; then
        echo "[$(date '+%H:%M:%S')] 检测到服务异常，正在修复..."
        auto_fix
      fi
      sleep "$interval"
    done
    ;;
  *)
    echo "用法: $0 {status|check|fix|daemon [间隔秒]}"
    echo ""
    echo "  status   — 详细检测所有服务状态（默认）"
    echo "  check    — 静默检查，适合 cron/CI"
    echo "  fix      — 检测异常后自动修复"
    echo "  daemon   — 持续检测模式（默认每60秒）"
    exit 1
    ;;
esac
