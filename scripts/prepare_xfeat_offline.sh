#!/usr/bin/env bash
# --------------------------------------------------------------------------- #
# 准备 XFeat（verlab/LEARNING-XFeat）离线安装包。
#
# 用法（在能访问外网的机器上执行）：
#   cd <本项目根目录>
#   bash scripts/prepare_xfeat_offline.sh
#
# 产物：
#   xfeat-offline/
#     xfeat/                  # pip install -e . 的源码包
#     wheels/                 # 依赖 whl（含 torch/torchvision 等，按当前平台）
#     install_offline.sh      # 在目标机执行：装依赖 + pip install -e xfeat/
#
# 然后把整个 xfeat-offline/ 目录传到目标机，执行 install_offline.sh。
# --------------------------------------------------------------------------- #
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

OUT_DIR="$HERE/xfeat-offline"
SRC_DIR="$OUT_DIR/xfeat"
WHEEL_DIR="$OUT_DIR/wheels"

rm -rf "$OUT_DIR"
mkdir -p "$SRC_DIR" "$WHEEL_DIR"

echo "=== [1/4] 克隆 verlab/LEARNING-XFeat ==="
if [ -d "$SRC_DIR/.git" ]; then
  echo "  已存在，跳过克隆"
else
  # 试 main 分支，再 master
  if ! git clone --depth 1 --branch main https://github.com/verlab/LEARNING-XFeat.git "$SRC_DIR" 2>/dev/null; then
    git clone --depth 1 --branch master https://github.com/verlab/LEARNING-XFeat.git "$SRC_DIR"
  fi
fi

echo "=== [2/4] 下载 XFeat 及其依赖到 wheels/ ==="
# 先装到临时 venv 解析完整依赖，再 download 成 whl
TMP_VENV="$(mktemp -d)/xfeat-venv"
python3 -m venv "$TMP_VENV"
# shellcheck disable=SC1091
source "$TMP_VENV/bin/activate"
pip install --upgrade pip wheel >/dev/null

# 安装 XFeat（解析依赖）
pip install -e "$SRC_DIR" 2>&1 | tail -5 || {
  echo "  ⚠ 直接 install -e 失败，尝试仅下载依赖..."
  pip install -e "$SRC_DIR" --no-build-isolation 2>&1 | tail -5
}

# 把当前 venv 里 XFeat 依赖（含 XFeat 自身）的所有 whl 拷到 wheels/
pip freeze | grep -iE "xfeat|torch|kornia|opencv|numpy|einops" | while read -r pkg; do
  name="${pkg%%==*}"
  echo "  下载: $pkg"
  pip download "$pkg" --no-deps --dest "$WHEEL_DIR" 2>/dev/null || true
done
deactivate
rm -rf "$TMP_VENV"

echo "=== [3/4] 写 install_offline.sh ==="
cat > "$OUT_DIR/install_offline.sh" <<'INSTALL'
#!/usr/bin/env bash
# 目标机离线安装 XFeat。
# 用法：bash xfeat-offline/install_offline.sh [venv路径，默认 .venv]
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${1:-$PWD/.venv}"

if [ ! -d "$VENV" ]; then
  echo "创建 venv: $VENV"
  python3 -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"

echo "=== 安装依赖 whl ==="
pip install --no-index --find-links "$HERE/wheels" xfeat 2>/dev/null || true

echo "=== 安装 XFeat 源码（可编辑） ==="
pip install --no-build-isolation -e "$HERE/xfeat"

echo "=== 验证 ==="
python -c "
import xfeat
print('xfeat OK:', xfeat.__version__ if hasattr(xfeat,'__version__') else xfeat.__file__)
print('符号:', [s for s in dir(xfeat) if not s.startswith('_')][:10])
"
echo "=== 完成 ==="
INSTALL
chmod +x "$OUT_DIR/install_offline.sh"

echo "=== [4/4] 完成 ==="
echo "产物目录: $OUT_DIR"
echo "  文件数: $(find "$OUT_DIR" -type f | wc -l)"
echo ""
echo "下一步："
echo "  1. 把 $OUT_DIR 整个目录传到目标机"
echo "  2. 在目标机执行: bash $OUT_DIR/install_offline.sh"
echo ""
echo "注意：wheels/ 里的 torch/torchvision 等平台相关包需与目标机"
echo "      (OS + Python 版本 + CPU/GPU) 一致；如冲突，在目标机"
echo "      用 pip install <pkg> --no-index --find-links wheels/ 逐个排除。"
