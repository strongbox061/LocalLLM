#!/usr/bin/env bash
# The bash -c checks get their values as arguments ($1...), so their text is single-quoted on purpose.
# shellcheck disable=SC2016
# System checks (S, no model): the live setup as it runs now, against what the repo and the model profile say.
#   tests/system-checks.sh            this PC: llama-server (served as the profile says, the alias, the API key in
#                                     split mode), Pi's settings, SearXNG, the search gateway and its dashboard,
#                                     ./localllm.sh status, OCR, and tests/restorability-check.sh
#   tests/system-checks.sh --helper   also the helper PC over SSH (split mode): its services' own report, the main
#                                     PC's llama-server reached from there, Open WebUI's settings and chat type
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

echo "Pi's settings (~/.pi/agent), as the installers write them"
PI_DIR="${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}"
key="none"
[[ "$LOCALLLM_MODE" == split ]] && key="$(cat "$LLAMA_API_KEY_FILE" 2>/dev/null)"
check "models.json: llama-server's address, the alias, the mode's key" python3 -c '
import json, sys; p = json.load(open(sys.argv[1]))["providers"]["llamacpp"]
assert p["baseUrl"] == sys.argv[2] + "/v1" and [m["id"] for m in p["models"]] == [sys.argv[3]]
assert p.get("apiKey") == sys.argv[4]' "$PI_DIR/models.json" "$LLAMA_URL" "$MODEL_ALIAS" "$key"
check "settings.json: compaction on, sized from the profile" python3 -c '
import json, sys; c = json.load(open(sys.argv[1]))["compaction"]
assert c == {"enabled": True, "reserveTokens": int(sys.argv[2]), "keepRecentTokens": int(sys.argv[3])}, c' \
    "$PI_DIR/settings.json" "$((MAX_TOKENS + 4096))" "$(((CTX_PER_SLOT - MAX_TOKENS) / 5))"
check "model.json (for the extensions): the profile's alias, slots, context and answer length" python3 -c '
import json, sys; m = json.load(open(sys.argv[1]))
assert (m["profile"], m["alias"], m["slots"], m["contextPerSlot"], m["maxTokens"]) == (sys.argv[2], sys.argv[3], *map(int, sys.argv[4:7])), m' \
    "$MODEL_JSON_FILE" "$MODEL_PROFILE" "$MODEL_ALIAS" "$SLOTS" "$CTX_PER_SLOT" "$MAX_TOKENS"

echo "Search: SearXNG and the search gateway"
check "SearXNG answers" test "$(code "$SEARXNG_URL/")" = 200
check "the search gateway answers (/healthz)" test "$(code "$SEARCH_GATEWAY_URL/healthz")" = 200
usage="$(curl -s -m 30 "$SEARCH_GATEWAY_URL/usage.json")"
check "its dashboard's numbers (/usage.json): every search service, with a status" python3 -c '
import json, sys; u = json.loads(sys.argv[1])
assert u["services"] and all("status" in s and "id" in s for s in u["services"])' "$usage"
check "the dashboard page loads" bash -c 'curl -s -m 10 "$1/dashboard" | grep -qi "<html"' _ "$SEARCH_GATEWAY_URL"

echo "./localllm.sh status"
status="$(main status all 2>&1)"
check "every service of this mode is running" bash -c '
rows="$(sed -nE "/^SERVICE /,/^\$/p" <<< "$1" | tail -n +2 | grep -E "(main|helper) PC")"
[[ -n "$rows" ]] && ! grep -vE " running\$" <<< "$rows"' _ "$status"
check "it names the last backup" has "$status" "Last backup: "

echo "OCR (tools/ocr/ocr-pdfs.sh): a scanned page gets a text layer"
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

echo "Restorability (tests/restorability-check.sh)"
rout="$("$REPO/tests/restorability-check.sh" 2>&1)"
check "everything is covered and restore.sh brings the backup back" test $? -eq 0
grep -E "^ *FAIL|not covered|missing" <<< "$rout" | sed 's/^/        /'

if [[ $WITH_HELPER -eq 1 ]]; then
    echo "The helper PC (over SSH)"
    if [[ "$LOCALLLM_MODE" != split ]]; then
        echo "  skip  single mode: everything runs on this PC"
    elif ! helper_ok 2>/dev/null; then
        check "the helper answers over SSH" false
    else
        from_helper="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["services"]["llama"]["from_helper"])' "$ENDPOINTS_FILE")"
        # reach <url>: the HTTP status the helper gets (PowerShell throws on 4xx; the status is in the exception).
        reach() { helper_ssh "try { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 10 '$1').StatusCode } catch { [int]\$_.Exception.Response.StatusCode }" | tr -d '\r'; }
        check "it reaches this PC's llama-server" test "$(reach "$from_helper/health")" = 200
        check "... which refuses it without the API key" test "$(reach "$from_helper/v1/models")" = 401
        if ! apps_has owui; then
            echo "  skip  Open WebUI checks (LOCALLLM_APPS=$LOCALLLM_APPS)"
        else
            settings="$(helper_task settings-status 2>/dev/null | tr -d '\r' | tail -1)"
            expected_embedding="$(python3 -c '
import ast, sys
for node in ast.parse(open(sys.argv[1]).read()).body:
    if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "EMBEDDINGS":
        print(ast.literal_eval(node.value)["bge-m3"]["RAG_EMBEDDING_MODEL"])' "$REPO/open-webui/sync.py")"
            check "Open WebUI: the model connection is this PC's llama-server" python3 -c '
import json, sys; assert sys.argv[2] + "/v1" in json.loads(sys.argv[1])["config"]["openai.api_base_urls"]' "$settings" "$from_helper"
            check "Open WebUI: document search with $expected_embedding, hybrid search on" python3 -c '
import json, sys; c = json.loads(sys.argv[1])["config"]
assert c["rag.embedding_model"] == sys.argv[2] and c["rag.enable_hybrid_search"] is True, c' "$settings" "$expected_embedding"
            for f in "$CHAT_TYPES_DIR"/*.json; do
                [[ -f "$f" ]] || continue
                id="$(basename -- "$f" .json)"
                check "chat type \"$id\": its preset has only the lookup tool and the filter, web search off" python3 -c '
import json, sys; sys.path.insert(0, sys.argv[3]); import chat_types as c
p = json.loads(sys.argv[1])["presets"][sys.argv[2]]
assert p == {"toolIds": [c.TOOL_ID], "filterIds": [c.FILTER_ID], "web_search": False}, p' "$settings" "$id" "$REPO/open-webui"
            done
        fi
        load_helper_env
        counts="$(helper_task check "$HELPER_DATA_DIR" 2>/dev/null | tr -d '\r' | tail -1)"
        check "Open WebUI's and the search gateway's databases pass the integrity check" python3 -c '
import json, sys; d = json.loads(sys.argv[1])
assert d["webui"]["integrity"] == "ok" and d["gateway"]["integrity"] == "ok", d' "$counts"
    fi
fi

echo "system-checks: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
