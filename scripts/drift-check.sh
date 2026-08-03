#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
errors=0
warnings=0

fail() {
  echo "[FAIL] $*" >&2
  errors=$((errors + 1))
}

warn() {
  echo "[WARN] $*" >&2
  warnings=$((warnings + 1))
}

"$ROOT_DIR/scripts/validate-specs.sh"

for required in \
  docs/README.md \
  docs/ubiquitous-language.md \
  docs/context-map.md \
  contexts/README.md \
  specs/README.md; do
  if [[ ! -s "$ROOT_DIR/$required" ]]; then
    fail "缺少权威文档：$required"
  fi
done

while IFS= read -r legacy; do
  name="$(basename "$legacy" .md)"
  if [[ ! -s "$ROOT_DIR/specs/$name/spec.md" ]]; then
    fail "$(basename "$legacy"): 没有对应 specs/$name/spec.md"
  elif ! grep -qE "\.\./specs/$name/spec\.md" "$legacy"; then
    fail "$(basename "$legacy"): 没有指向权威规格"
  fi
done < <(find "$ROOT_DIR/spec" -maxdepth 1 -type f -name '[0-9][0-9][0-9]-*.md' | sort)

while IFS= read -r tracked; do
  case "$tracked" in
    las/.gitkeep|las/*.txt|query_images/.gitkeep|projections/.gitkeep|logs/.gitkeep)
      ;;
    las/*|query_images/*|projections/*|logs/*|.DS_Store)
      warn "历史运行产物仍被 Git 跟踪：$tracked"
      ;;
  esac
done < <(git -C "$ROOT_DIR" ls-files)

if [[ "$errors" -gt 0 ]]; then
  echo "漂移检查失败：$errors 个错误，$warnings 个警告。" >&2
  exit 1
fi

echo "漂移检查通过：0 个错误，$warnings 个历史警告。"
