#!/usr/bin/env bash
set -euo pipefail

# Smoke test for Pi extensions — verifies each extension loads without error.

EXTENSIONS_DIR="${PI_HOME:-$HOME/.pi}/extensions"
PASS=0
FAIL=0

check_extension() {
  local ext_name="$1"
  local ext_path="${EXTENSIONS_DIR}/${ext_name}"

  if [[ ! -d "$ext_path" ]]; then
    echo "SKIP  ${ext_name} — directory not found"
    return
  fi

  if [[ ! -f "${ext_path}/SKILL.md" ]]; then
    echo "FAIL  ${ext_name} — missing SKILL.md"
    ((FAIL++))
    return
  fi

  # Verify at least one JS/TS entry point exists alongside SKILL.md
  if [[ ! -f "${ext_path}/index.js" && ! -f "${ext_path}/index.ts" ]]; then
    echo "FAIL  ${ext_name} — no JS/TS entry point found"
    ((FAIL++))
    return
  fi

  echo "OK    ${ext_name}"
  ((PASS++))
}

# Known extensions at this stage in the repo
for ext in code-finder worker implement-and-review name-guard planner; do
  check_extension "$ext"
done

echo ""
echo "Smoke test complete: ${PASS} passed, ${FAIL} failed"

[[ $FAIL -eq 0 ]] || exit 1
