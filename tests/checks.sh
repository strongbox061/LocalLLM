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
set -uo pipefail

REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
WITH_HELPER=0
case "${1:-}" in
    "") ;;
    --helper) WITH_HELPER=1 ;;
    *) echo "Usage: $0 [--helper]" >&2; exit 2 ;;
esac
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
PASS=0 FAIL=0

check() {   # check <name> <command...>
    local name="$1"; shift
    if "$@" >/dev/null 2>&1; then PASS=$((PASS + 1)); echo "  ok    $name"
    else FAIL=$((FAIL + 1)); echo "  FAIL  $name"; fi
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

# localllm.sh brings lib/common.sh (the addresses, LOCALLLM_MODE, the key file) and lib/helper.sh (helper_ssh,
# helper_task); lib/model.sh the profile's values.
# shellcheck source=localllm.sh
source "$REPO/localllm.sh"
# shellcheck source=lib/model.sh
source "$REPO/lib/model.sh"
load_model_profile >/dev/null || { echo "The model profile doesn't load." >&2; exit 1; }
[[ -z "$ENDPOINTS_ERROR" ]] || { echo "$ENDPOINTS_ERROR" >&2; exit 1; }
AUTH=()
[[ "$LOCALLLM_MODE" == split ]] && AUTH=(-H "Authorization: Bearer $(cat "$LLAMA_API_KEY_FILE" 2>/dev/null)")
echo "Mode: $LOCALLLM_MODE, model profile: $MODEL_PROFILE"

echo "llama-server, as the model profile says"
check "answers (/health)" test "$(code "$LLAMA_URL/health")" = 200
models="$(curl -s -m 10 "${AUTH[@]}" "$LLAMA_URL/v1/models")"
check "serves the model as \"$MODEL_ALIAS\", and only that" \
    python3 -c 'import json, sys; assert [m["id"] for m in json.loads(sys.argv[1])["data"]] == [sys.argv[2]]' "$models" "$MODEL_ALIAS"
props="$(curl -s -m 10 "${AUTH[@]}" "$LLAMA_URL/props")"
check "runs the profile's model file, $SLOTS slots of $CTX_PER_SLOT tokens" python3 -c '
import json, os, sys; p = json.loads(sys.argv[1])
assert os.path.basename(p["model_path"]) == sys.argv[2], p["model_path"]
assert p["total_slots"] == int(sys.argv[3]) and p["default_generation_settings"]["n_ctx"] == int(sys.argv[4])' \
    "$props" "$MODEL_FILE" "$SLOTS" "$CTX_PER_SLOT"
if [[ "$LOCALLLM_MODE" == split ]]; then
    check "split mode: a request without the API key is refused" test "$(code "$LLAMA_URL/v1/models")" = 401
fi

echo "Search: SearXNG and the search gateway"
check "SearXNG answers" test "$(code "$SEARXNG_URL/")" = 200
check "the search gateway answers (/healthz)" test "$(code "$SEARCH_GATEWAY_URL/healthz")" = 200
usage="$(curl -s -m 30 "$SEARCH_GATEWAY_URL/usage.json")"
check "its dashboard's numbers (/usage.json): every search service, with a status" python3 -c '
import json, sys; u = json.loads(sys.argv[1])
assert u["services"] and all("status" in s and "id" in s for s in u["services"])' "$usage"
check "the dashboard page loads" bash -c 'curl -s -m 10 "$1/dashboard" | grep -qi "<html"' _ "$SEARCH_GATEWAY_URL"

# --- OCR checks ---
if command -v ocrmypdf >/dev/null && command -v magick >/dev/null && command -v pdftotext >/dev/null; then
    font="$(fc-match -f '%{file}' 'DejaVu Sans' 2>/dev/null)"
    mkdir -p "$TMP/scans"
    magick -size 2400x600 xc:white -font "$font" -fill black -pointsize 90 -annotate +80+300 "Invoice number 4721 for repairs" \
        "$TMP/page.png" 2>/dev/null && magick "$TMP/page.png" -density 200 "$TMP/scans/scan.pdf" 2>/dev/null
    check "the sample scan has no text of its own" test -z "$(pdftotext "$TMP/scans/scan.pdf" - 2>/dev/null | tr -d '[:space:]')"
    "$REPO/tools/ocr/ocr-pdfs.sh" "$TMP/scans" eng >/dev/null 2>&1
    check "its OCR copy has the words" bash -c 'pdftotext "$1" - | grep -q "Invoice number 4721"' _ "$TMP/scans/ocr/scan.pdf"
else
    echo "  skip  OCR: ocrmypdf, ImageMagick or pdftotext isn't on this PC (installed where Open WebUI runs)"
fi

# --- OCR checks ---
if command -v tesseract &>/dev/null; then
    check "tesseract available" "tesseract --version 2>&1 | head -1"
else
    echo "SKIP: tesseract not installed"
fi

echo "Restorability (tests/restorability-check.sh)"
rout="$(\"$REPO/tests/restorability-check.sh\" 2>&1)"
check "everything is covered and restore.sh brings the backup back" test $? -eq 0
grep -E "^ *FAIL|not covered|missing" <<< "$rout" | sed 's/^/        /'

echo "system-checks: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
