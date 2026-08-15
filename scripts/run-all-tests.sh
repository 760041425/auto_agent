#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-fast}"

if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi

cd "$ROOT_DIR"

# macOS OpenMP 兼容：torch+faiss+scipy+PIL 共存时 libomp.dylib 重复初始化 → SIGABRT
export KMP_DUPLICATE_LIB_OK="${KMP_DUPLICATE_LIB_OK:-TRUE}"

case "$MODE" in
  fast)
    "$PYTHON_BIN" -m pytest -q \
      -m "not integration and not slow and not system"
    ;;
  all)
    "$PYTHON_BIN" -m pytest -q
    ;;
  *)
    echo "用法: $0 {fast|all}" >&2
    exit 2
    ;;
esac
