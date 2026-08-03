#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT="${1:-$ROOT_DIR/reports/traceability/traceability.md}"
TMP_FILE="$(mktemp)"
trap 'rm -f "$TMP_FILE"' EXIT

mkdir -p "$(dirname "$OUTPUT")"

{
  echo "# 规格追踪报告"
  echo
  echo "由 \`scripts/traceability-report.sh\` 生成。状态来自规格文件，不代表额外验收。"
  echo
  echo "| 规格 | AC | TASK | TL | 风险 |"
  echo "| --- | ---: | ---: | ---: | ---: |"

  while IFS= read -r spec_dir; do
    feature_id="$(basename "$spec_dir")"
    ac_count="$(grep -oE 'AC-[0-9]{3}-[0-9]{2}' "$spec_dir/spec.md" | sort -u | wc -l | tr -d ' ')"
    task_count="$(grep -oE 'TASK-[0-9]{3}-[0-9]{2}' "$spec_dir/tasks.md" | sort -u | wc -l | tr -d ' ')"
    test_count="$(grep -oE 'TL-[0-9]{3}-[0-9]{2}' "$spec_dir/testlist.md" | sort -u | wc -l | tr -d ' ')"
    risk_count="$(grep -oE 'RISK-[0-9]{3}-[0-9]{2}' "$spec_dir/risks.md" | sort -u | wc -l | tr -d ' ')"
    echo "| [$feature_id](../../specs/$feature_id/spec.md) | $ac_count | $task_count | $test_count | $risk_count |"
  done < <(find "$ROOT_DIR/specs" -mindepth 1 -maxdepth 1 -type d | sort)

  echo
  echo "## 测试场景映射"
  echo

  first_spec=true
  while IFS= read -r spec_dir; do
    feature_id="$(basename "$spec_dir")"
    if "$first_spec"; then
      first_spec=false
    else
      echo
    fi
    echo "### $feature_id"
    echo
    grep -E '^\| \*\*TL-[0-9]{3}-[0-9]{2}\*\*' "$spec_dir/testlist.md" || true
  done < <(find "$ROOT_DIR/specs" -mindepth 1 -maxdepth 1 -type d | sort)
} > "$TMP_FILE"

mv "$TMP_FILE" "$OUTPUT"
trap - EXIT
echo "已生成追踪报告：$OUTPUT"
