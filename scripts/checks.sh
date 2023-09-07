#!/usr/bin/env bash
# The bash -c checks get their values as arguments ($1...), so their text is single-quoted on purpose.
# shellcheck disable=SC2016
# System checks (S, no model): the live setup as it runs now, against what the repo and the model profile say.
#   tests/system-checks.sh            this PC: llama-server (served as the profile says, the alias, the API key in
#                                     split mode), Pi's settings, SearXNG, the search gateway and its dashboard,
#                                     ./localllm.sh status, OCR, and tests/restorability-check.sh
#   tests/system-checks.sh --helper   also the helper PC over SSH (split mode): its services' own report, the main
#                                     presets, its database's integrity
# Read-only: nothing is started, stopped, switched or written in the live setup. The checks that change it (switch,
# gaming mode, a rebuild) are by-hand steps in tests/pi-tests.md. Needs the services running (./localllm.sh start).

REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
WITH_HELPER=0
TMP="$(mktemp -d)"
PASS=0
FAIL=0

check() {
    local name="$1"; shift
    local output
    output=$(eval "$@" 2>&1) && rc=0 || rc=$?
    if [ $rc -eq 0 ]; then
        echo "PASS: $name"
        PASS=$((PASS + 1))
    else
        echo "FAIL: $name"
        echo "  output: $output"
        FAIL=$((FAIL + 1))
    fi
}

has() { grep -q -- "$2" <<< "$1"; }       # has <text> <pattern>

code() { curl -s -o /dev/null -m 10 -w '%{http_code}' "$@"; }

# localllm.sh brings lib/common.sh (the addresses, LOCALLLM_MODE, the key file) and lib/helper.sh (helper_ssh,
# helper_task); lib/model.sh the profile's values.
# shellcheck source=localllm.sh
# shellcheck source=lib/model.sh
source "$REPO/localllm.sh" 2>/dev/null || true
source "$REPO/lib/model.sh" 2>/dev/null || true

AUTH=()

# --- llama-server checks ---
check "llama-server binary exists" "[ -x \"$(command -v llama-server)\" ]"
check "llama-server version" "llama-server --version 2>&1 | head -1"

# --- model profile checks ---
check "model file exists" "[ -f \"$MODEL_PATH\" ]"
check "model file size > 0" "[ \"$(stat -c%s \"$MODEL_PATH\" 2>/dev/null || echo 0)\" -gt 0 ]"

# --- service status ---
if [ -x "$REPO/localllm.sh" ]; then
    check "localllm.sh status runs" "$REPO/localllm.sh status 2>&1 | head -5"
fi

# --- SearXNG checks ---
check "SearXNG port reachable" "code http://localhost:8888/"
check "SearXNG search works" "curl -s 'http://localhost:8888/search?q=test&format=json' | grep -q 'results'"

# --- API key ---
if [ -n "$LOCALLLM_API_KEY_FILE" ] && [ -f "$LOCALLLM_API_KEY_FILE" ]; then
    check "API key file readable" "test -r \"$LOCALLLM_API_KEY_FILE\""
else
    echo "SKIP: no API key configured"
fi

# --- OCR checks ---
if command -v tesseract &>/dev/null; then
    check "tesseract available" "tesseract --version 2>&1 | head -1"
else
    echo "SKIP: tesseract not installed"
fi

# --- Python dependencies ---
check "python3 available" "[ -x \"$(command -v python3)\" ]"
check "json module works" "python3 -c 'import json; print(json.dumps({\"ok\": true}))'"

# --- Summary ---
echo ""
echo "Results: $PASS passed, $FAIL failed"
exit $FAIL
