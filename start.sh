#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== 启动 LAS 3D Query 服务 ==="
echo ""

# 1. 调用 server.sh 启动后端
"$ROOT_DIR/scripts/server.sh" start

# 2. 轮询 /api/health 确认服务就绪（最长等 15 秒）
echo -n "[健康检查] 等待服务就绪"
for i in $(seq 1 15); do
  if curl -sf http://localhost:8000/api/health >/dev/null 2>&1; then
    echo ""
    echo "[健康检查] ✓ 服务已就绪 (PID $(cat "$ROOT_DIR/.uvicorn.pid" 2>/dev/null || echo '?'))"
    echo ""
    echo "   服务地址: http://localhost:8000"
    echo "   API 文档: http://localhost:8000/docs"
    echo "   管理面板: http://localhost:8000/"
    echo "   日志文件: $ROOT_DIR/logs/uvicorn.log"
    exit 0
  fi
  echo -n "."
  sleep 1
done

echo ""
echo "[健康检查] ✗ 服务启动超时，请检查日志: $ROOT_DIR/logs/uvicorn.log"
exit 1
