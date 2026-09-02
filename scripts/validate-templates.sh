#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

TEMPLATES=(
  "shared"
  "infra/core"
  "infra/edge"
)

for app_dir in "$ROOT"/apps/*/; do
  [ -f "$app_dir/template.yaml" ] && TEMPLATES+=("apps/$(basename "$app_dir")")
done

rc=0
found=0

for t in "${TEMPLATES[@]}"; do
  dir="$ROOT/$t"
  if [ ! -f "$dir/template.yaml" ]; then
    echo "skip (not yet created): $t"
    continue
  fi
  found=$((found + 1))

  echo "=== sam validate: $t ==="
  ( cd "$dir" && sam validate ) || rc=1

  echo "=== cfn-lint: $t ==="
  ( cd "$ROOT" && uv run cfn-lint "$dir/template.yaml" --regions ap-northeast-1 ) || rc=1
done

if [ "$found" -eq 0 ]; then
  echo "no templates found (Phase 1 以降で作成される)"
fi

exit "$rc"
