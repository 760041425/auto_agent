#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SPEC_ROOT="$ROOT_DIR/specs"
REQUIRED_FILES=(
  spec.md
  clarify.md
  plan.md
  tasks.md
  checklist.md
  testlist.md
  risks.md
  decisions.md
)

errors=0
checked=0

fail() {
  echo "[FAIL] $*" >&2
  errors=$((errors + 1))
}

if [[ ! -d "$SPEC_ROOT" ]]; then
  fail "缺少 specs/ 目录"
else
  while IFS= read -r spec_dir; do
    checked=$((checked + 1))
    feature_id="$(basename "$spec_dir")"

    if [[ ! "$feature_id" =~ ^[0-9]{3}-[a-z0-9][a-z0-9-]*$ ]]; then
      fail "$feature_id: 目录名应为 <三位序号>-<英文短名>"
    fi

    for required in "${REQUIRED_FILES[@]}"; do
      path="$spec_dir/$required"
      if [[ ! -s "$path" ]]; then
        fail "$feature_id: 缺少或为空 $required"
      fi
    done

    if [[ -s "$spec_dir/spec.md" ]] && ! grep -qE 'AC-[0-9]{3}-[0-9]{2}' "$spec_dir/spec.md"; then
      fail "$feature_id/spec.md: 缺少稳定 AC-ID"
    fi
    if [[ -s "$spec_dir/tasks.md" ]] && ! grep -qE 'TASK-[0-9]{3}-[0-9]{2}' "$spec_dir/tasks.md"; then
      fail "$feature_id/tasks.md: 缺少稳定 TASK-ID"
    fi
    if [[ -s "$spec_dir/testlist.md" ]] && ! grep -qE 'TL-[0-9]{3}-[0-9]{2}' "$spec_dir/testlist.md"; then
      fail "$feature_id/testlist.md: 缺少稳定 TL-ID"
    fi
    if [[ -s "$spec_dir/risks.md" ]] && ! grep -qE 'RISK-[0-9]{3}-[0-9]{2}' "$spec_dir/risks.md"; then
      fail "$feature_id/risks.md: 缺少稳定 RISK-ID"
    fi
  done < <(find "$SPEC_ROOT" -mindepth 1 -maxdepth 1 -type d | sort)
fi

if [[ "$checked" -eq 0 ]]; then
  fail "没有发现特性规格包"
fi

if [[ "$errors" -gt 0 ]]; then
  echo "规格校验失败：$errors 个问题，已检查 $checked 个规格包。" >&2
  exit 1
fi

echo "规格校验通过：$checked 个规格包，八件套和稳定 ID 均已检查。"
