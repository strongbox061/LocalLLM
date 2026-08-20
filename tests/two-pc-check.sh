#!/usr/bin/env bash
# The bash -c checks get their values as arguments ($1...), so their text is single-quoted on purpose.
# shellcheck disable=SC2016
# Two-PC setup checks (S, no model, no helper PC needed): where each service runs per mode (lib/endpoints.py), the
# addresses lib/common.sh gives the scripts, ./localllm.sh's arguments and its helper-unreachable path, setup-helper's
# local.env and ~/.ssh/config edits, Pi's API key in split mode and llama-server refusing split mode without a key,
# the settings and chat types pages in split mode.
# Everything runs with a temporary HOME; the live setup is only read (the main PC's own services' health addresses).
#   tests/two-pc-check.sh     (run by tests/guard-tests.mjs)
set -uo pipefail

REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
PASS=0 FAIL=0
check() {   # check <name> <command...>
    local name="$1"; shift
    if "$@" >/dev/null 2>&1; then PASS=$((PASS + 1)); echo "  ok    $name"
    else FAIL=$((FAIL + 1)); echo "  FAIL  $name"; fi
}
has() { grep -q -- "$2" <<< "$1"; }       # has <text> <pattern>
hasnt() { ! grep -q -- "$2" <<< "$1"; }

ep() { env -i PATH="$PATH" "$@" python3 "$REPO/lib/endpoints.py" print 2>&1; }
field() { python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(d["services"][sys.argv[2]][sys.argv[3]])' "$1" "$2" "$3" 2>/dev/null; }

echo "lib/endpoints.py"
single="$(ep)"
check "single mode: every service on the main PC, 127.0.0.1" \
    test "$(field "$single" open-webui url)$(field "$single" llama url)" = "http://127.0.0.1:3000http://127.0.0.1:8081"
check "single mode: helper-only services don't run" test "$(field "$single" comfyui where)" = "None"
split="$(ep LOCALLLM_MODE=split HELPER_HOST=helper.example MAIN_HOST=main.example)"
check "split: llama-server stays on the main PC (127.0.0.1 here, the main PC's name from the helper)" \
    test "$(field "$split" llama url) $(field "$split" llama from_helper)" = "http://127.0.0.1:8081 http://main.example:8081"
check "split: the search gateway on the helper" test "$(field "$split" search-gateway url)" = "http://helper.example:8090"
check "split: Open WebUI over HTTPS on the helper's name (microphone needs a secure page)" \
    test "$(field "$split" open-webui url)" = "https://helper.example"
moved="$(ep LOCALLLM_MODE=split HELPER_HOST=h MAIN_HOST=m PLACE_KOKORO=main)"
check "PLACE_KOKORO=main takes the voice router along" \
    test "$(field "$moved" kokoro where)$(field "$moved" voice-router where)" = "mainmain"
check "refused: Kokoro and the voice router apart" \
    has "$(ep LOCALLLM_MODE=split HELPER_HOST=h MAIN_HOST=m PLACE_KOKORO=main PLACE_VOICE_ROUTER=helper)" "same PC"
check "refused: llama-server on the helper" has "$(ep LOCALLLM_MODE=split HELPER_HOST=h MAIN_HOST=m PLACE_LLAMA=helper)" "always runs"
check "refused: split without HELPER_HOST" has "$(ep LOCALLLM_MODE=split MAIN_HOST=m)" "needs HELPER_HOST"
check "refused: a mode that doesn't exist" has "$(ep LOCALLLM_MODE=splitt)" "single or split"
check "refused: invalid LOCALLLM_APPS (owui)" has "$(ep LOCALLLM_APPS=owui)" "pi or pi,owui"
pi_single="$(ep LOCALLLM_MODE=single LOCALLLM_APPS=pi)"
check "pi-only (single): llama, searxng, search-gateway, plannotator stay on main; OWUI + voice off" python3 -c '
import json, sys; d = json.loads(sys.argv[1]); s = d["services"]
assert d["apps"] == "pi"
assert [n for n, v in s.items() if v["where"] == "main"] == ["llama", "searxng", "plannotator", "search-gateway"]
assert all(s[n]["where"] is None for n in ("open-webui", "kokoro", "voice-router", "whisper", "docling", "small-model", "comfyui"))' "$pi_single"
pi_split="$(ep LOCALLLM_MODE=split LOCALLLM_APPS=pi HELPER_HOST=helper.example MAIN_HOST=main.example)"
check "pi-only (split): search-gateway stays on helper; all OWUI services off on both PCs" python3 -c '
import json, sys; d = json.loads(sys.argv[1]); s = d["services"]
assert d["apps"] == "pi"
assert s["search-gateway"]["where"] == "helper" and s["llama"]["where"] == "main"
assert all(s[n]["where"] is None for n in ("open-webui", "kokoro", "voice-router", "whisper", "docling", "small-model", "comfyui"))' "$pi_split"

echo "lib/common.sh"
H="$TMP/home"
mkdir -p "$H/.config/localllm"
printf 'LOCALLLM_MODE=split\nHELPER_HOST=helper.example\nHELPER_USER=someone\nMAIN_HOST=main.example\n' > "$H/.config/localllm/local.env"
urls() { HOME="$H" bash -c 'source "$1/lib/common.sh"; echo "$LOCALLLM_MODE $LLAMA_URL $SEARCH_GATEWAY_URL $WEBUI_URL"' _ "$REPO" 2>&1; }
check "split: the scripts get the helper's addresses" \
    test "$(urls)" = "split http://127.0.0.1:8081 http://helper.example:8090 https://helper.example"
check "endpoints.json is written for Pi" test -f "$H/.config/localllm/endpoints.json"
sleep 1
printf 'LOCALLLM_MODE=split\n' > "$H/.config/localllm/local.env"
check "settings that don't fit: a warning and the single-mode addresses" \
    bash -c '[[ "$1" == *"needs HELPER_HOST"* && "$1" == *" http://127.0.0.1:8081 http://127.0.0.1:8090 http://127.0.0.1:3000"* ]]' _ "$(urls | tr '\n' ' ')"

printf 'LOCALLLM_MODE=single\n' > "$H/.config/localllm/local.env"
check "a changed local.env regenerates the addresses" test "$(urls)" = "single http://127.0.0.1:8081 http://127.0.0.1:8090 http://127.0.0.1:3000"
