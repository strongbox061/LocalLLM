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
check "pi-only model profile: folds all slots into 1 with 2x context" bash -c '
HOME="$1" LOCALLLM_APPS=pi bash -c "source \"$2/lib/model.sh\"; load_model_profile >/dev/null; echo \"\$SLOTS \$CTX_PER_SLOT\""' _ "$TMP" "$REPO"
pi_slot_ctx="$(HOME="$TMP" LOCALLLM_APPS=pi bash -c 'source "$1/lib/model.sh"; load_model_profile >/dev/null; echo "$SLOTS $CTX_PER_SLOT"' _ "$REPO")"
check "... 1 slot of 131072 tokens for the default profile" test "$pi_slot_ctx" = "1 131072"

echo "lib/common.sh"
H="$TMP/home"
mkdir -p "$H/.config/localllm"
printf 'LOCALLLM_MODE=split\nHELPER_HOST=helper.example\nHELPER_USER=someone\nMAIN_HOST=main.example\n' > "$H/.config/localllm/local.env"
urls() { HOME="$H" bash -c 'source "$1/lib/common.sh"; echo "$LOCALLLM_MODE $LLAMA_URL $SEARCH_GATEWAY_URL $WEBUI_URL"' _ "$REPO" 2>&1; }
check "split: the scripts get the helper's addresses" \
    test "$(urls)" = "split http://127.0.0.1:8081 http://helper.example:8090 https://helper.example"
check "endpoints.json is written for Pi" test -f "$H/.config/localllm/endpoints.json"
sleep 1
printf 'LOCALLLM_MODE=single\n' > "$H/.config/localllm/local.env"
check "a changed local.env regenerates the addresses" test "$(urls)" = "single http://127.0.0.1:8081 http://127.0.0.1:8090 http://127.0.0.1:3000"
sleep 1
printf 'LOCALLLM_MODE=split\n' > "$H/.config/localllm/local.env"
check "settings that don't fit: a warning and the single-mode addresses" \
    bash -c '[[ "$1" == *"needs HELPER_HOST"* && "$1" == *" http://127.0.0.1:8081 http://127.0.0.1:8090 http://127.0.0.1:3000"* ]]' _ "$(urls | tr '\n' ' ')"

echo "localllm.sh"
printf 'LOCALLLM_MODE=single\n' > "$H/.config/localllm/local.env"
L() { HOME="$H" "$REPO/localllm.sh" "$@" 2>&1; }
check "single mode: 'start helper' is refused" has "$(L start helper)" "single mode"
check "an unknown service is refused" has "$(L stop nonsense)" "No service 'nonsense'"
check "an unknown command prints the usage" has "$(L frobnicate)" "localllm.sh start|stop"
check "single mode: restore-helper-data is refused (the data is here)" has "$(L restore-helper-data)" "switch split moves it there"
check "settings that don't fit: localllm.sh refuses" \
    bash -c 'printf "LOCALLLM_MODE=split\n" > "$1"; out="$(HOME="$2" "$3/localllm.sh" status 2>&1)"; [[ $? -ne 0 && "$out" == *"needs HELPER_HOST"* ]]' \
    _ "$H/.config/localllm/local.env" "$H" "$REPO"
# Split mode with a helper that can't be reached (no SSH config in this HOME): the main PC's part still answers.
printf 'LOCALLLM_MODE=split\nHELPER_HOST=helper.invalid\nHELPER_USER=someone\nMAIN_HOST=main.example\n' > "$H/.config/localllm/local.env"
out="$(L status)"
check "split, helper unreachable: status lists the main PC's services" has "$out" "llama .*main PC"
check "... and the helper's as unreachable" has "$out" "open-webui .*helper PC .*helper unreachable"
out="$(L stop helper)"
check "split, helper unreachable: 'stop helper' says so and fails" \
    bash -c '[[ "$1" == *"can'"'"'t be reached"* && "$1" == *"Not done: helper PC"* ]]' _ "$out"

check "single mode keeps HELPER_HOST from local.env (setup-helper needs it before the switch)" \
    bash -c 'printf "HELPER_HOST=kept.example\n" > "$1"; [[ "$(HOME="$2" bash -c "source \"\$0/lib/common.sh\"; echo \$HELPER_HOST" "$3")" == kept.example ]]' \
    _ "$H/.config/localllm/local.env" "$H" "$REPO"

echo "setup-helper's edits (local.env, ~/.ssh/config)"
S() { HOME="$H" bash -c 'source "$1/localllm.sh"; shift; "$@"' _ "$REPO" "$@" 2>&1; }
printf '# mine\nBACKUP_KEEP_COUNT="8"\nHELPER_HOST="old"\n' > "$H/.config/localllm/local.env"
S set_local_env HELPER_HOST "new.example" >/dev/null
S set_local_env HELPER_USER "someone" >/dev/null
envtext="$(cat "$H/.config/localllm/local.env")"
check "set_local_env replaces a line and appends a new one, the rest kept" \
    test "$envtext" = $'# mine\nBACKUP_KEEP_COUNT="8"\nHELPER_HOST=new.example\nHELPER_USER=someone'
check "local.env stays yours only (600)" test "$(stat -c %a "$H/.config/localllm/local.env")" = 600
mkdir -p "$H/.ssh"
printf 'Host other\n    User me\n\nHost *\n    User everyone\n' > "$H/.ssh/config"
S write_ssh_config >/dev/null
cfg="$(cat "$H/.ssh/config")"
check "the Host block goes first (before Host *), with the helper's name and user" \
    bash -c '[[ "$(sed -n 2p <<< "$1")" == "Host localllm-helper" ]] && grep -q "HostName new.example" <<< "$1" && grep -q "User someone" <<< "$1"' _ "$cfg"
check "the rest of ~/.ssh/config is kept" bash -c 'grep -q "Host other" <<< "$1" && grep -q "User everyone" <<< "$1"' _ "$cfg"
check "running it again changes nothing" has "$(S write_ssh_config)" "up to date"
printf 'HELPER_HOST=newer.example\nHELPER_USER=someone\n' > "$H/.config/localllm/local.env"
S write_ssh_config >/dev/null
check "a new HELPER_HOST replaces the block (one block only)" \
    bash -c '[[ "$(grep -c "Host localllm-helper" "$1")" == 1 ]] && grep -q "HostName newer.example" "$1" && ! grep -q "new.example$" "$1"' _ "$H/.ssh/config"

# The Windows PCs in the tailnet, from a fake `tailscale status --json` (a Linux peer is left out).
mkdir -p "$TMP/bin"
cat > "$TMP/bin/tailscale" <<'FAKE'
#!/bin/sh
echo '{"BackendState":"Running","Peer":{"a":{"DNSName":"win-box.tailnet.example.","HostName":"WIN-BOX","OS":"windows"},"b":{"DNSName":"phone.tailnet.example.","HostName":"phone","OS":"android"}}}'
FAKE
chmod +x "$TMP/bin/tailscale"
check "setup-helper lists only the tailnet's Windows PCs, by DNS name and host name" \
    test "$(PATH="$TMP/bin:$PATH" S tailnet_windows_peers)" = $'win-box.tailnet.example\tWIN-BOX'
# The helper's own report (services.ps1 status -Json), read through a fake helper_ssh.
report() { HOME="$H" REPORT="$1" bash -c 'source "$1/localllm.sh"; helper_ok() { true; }; helper_ssh() { echo "$REPORT"; }; helper_state open-webui' _ "$REPO" 2>&1; }
check "status reads the helper's report (a list)" test "$(report '[{"name":"open-webui","state":"running"},{"name":"kokoro","state":"stopped"}]')" = running
check "... and a single service (PowerShell's ConvertTo-Json drops the list)" test "$(report '{"name":"open-webui","state":"stopped"}')" = stopped

echo "llama-server's API key in split mode"
printf 'LOCALLLM_MODE=split\nHELPER_HOST=h.example\nMAIN_HOST=m.example\n' > "$H/.config/localllm/local.env"
out="$(HOME="$H" "$REPO/services/llm-server.sh" 2>&1)"
check "split without a key: llama-server refuses to start" has "$out" "needs .*llama-api-key"
HOME="$H" bash -c 'source "$1/lib/common.sh"; ensure_llama_api_key' _ "$REPO" >/dev/null 2>&1
check "the key is made once, yours only (600)" test "$(stat -c %a "$H/.config/localllm/llama-api-key" 2>/dev/null)" = 600
HOME="$H" "$REPO/install/pi-models.sh" >/dev/null 2>&1
key="$(cat "$H/.config/localllm/llama-api-key")"
check "split: Pi sends the key, and models.json is yours only" \
    bash -c 'grep -q "\"apiKey\": \"$1\"" "$2" && [[ "$(stat -c %a "$2")" == 600 ]]' _ "$key" "$H/.pi/agent/models.json"
printf 'LOCALLLM_MODE=single\n' > "$H/.config/localllm/local.env"
HOME="$H" "$REPO/install/pi-models.sh" >/dev/null 2>&1
check "single: Pi sends no key again" grep -q '"apiKey": "none"' "$H/.pi/agent/models.json"

echo "lib/helper_config.py (the helper's config.json) and the deploy's file list"
EPS="$TMP/eps.json"
env -i PATH="$PATH" LOCALLLM_MODE=split HELPER_HOST=h.example MAIN_HOST=m.example python3 "$REPO/lib/endpoints.py" print > "$EPS"
printf '{"alias":"local","contextPerSlot":65536,"maxTokens":8192}\n' > "$TMP/model.json"
hc() { env -i PATH="$PATH" "$@" python3 "$REPO/lib/helper_config.py" "$EPS" "$TMP/model.json" 2>&1; }
cfg="$(hc HELPER_DATA_DIR='D:\LocalLLM' OPEN_WEBUI_VERSION=1.2.3)"
names() { python3 -c 'import json,sys; print(" ".join(s["name"] for s in json.loads(sys.argv[1])["services"]))' "$1" 2>/dev/null; }
check "every helper service, in start order (Open WebUI last)" \
    test "$(names "$cfg")" = "kokoro voice-router whisper docling small-model comfyui search-gateway open-webui"
check "the helper reaches llama-server and SearXNG by the main PC's name" \
    bash -c 'grep -q "\"llama_url\": \"http://m.example:8081\"" <<< "$1" && grep -q "http://m.example:8080/search" <<< "$1"' _ "$cfg"
check "the compaction threshold matches services/open-webui.sh (7/8 of the slot less the answer)" \
    bash -c 'grep -q "\"compaction_threshold\": 50176" <<< "$1"' _ "$cfg"
check "the small model comes from models/ (no model names elsewhere)" bash -c 'grep -q "\"alias\": \"helper-small\"" <<< "$1"' _ "$cfg"
check "HELPER_SMALL_MODEL=off and HELPER_IMAGES=off leave those services out" \
    test "$(names "$(hc HELPER_DATA_DIR='D:\LocalLLM' OPEN_WEBUI_VERSION=1 HELPER_SMALL_MODEL=off HELPER_IMAGES=off)")" = "kokoro voice-router whisper docling search-gateway open-webui"
check "refused: a data folder that isn't a Windows path" has "$(hc HELPER_DATA_DIR=/mnt/x OPEN_WEBUI_VERSION=1)" "full Windows path"
check "refused: no Open WebUI version known" has "$(hc HELPER_DATA_DIR='D:\L')" "version is unknown"
files="$(cd "$REPO" && bash -c 'source lib/common.sh >/dev/null 2>&1; source lib/helper.sh; git ls-files --cached --others --exclude-standard -- "${DEPLOY_PATHS[@]}" | grep -Ev "$DEPLOY_EXCLUDE"')"
check "the deploy sends the helper's scripts, the gateway and Open WebUI's tools" \
    bash -c 'grep -qx windows/services.ps1 <<< "$1" && grep -qx services/search_gateway.py <<< "$1" && grep -qx open-webui/sync.py <<< "$1"' _ "$files"
check "... but never bootstrap.ps1 or the main PC's own units and scripts" \
    bash -c '! grep -Eq "bootstrap|\.service$|llm-server\.sh|kokoro/docker" <<< "$1"' _ "$files"

echo "pages in split mode (settings, chat types)"
# A HOME of its own: split mode, a helper that can't be reached, one chat type file.
P="$TMP/pages"
mkdir -p "$P/.config/localllm/chat-types/.deleted"
printf 'LOCALLLM_MODE=split\nHELPER_HOST=helper.invalid\nHELPER_USER=someone\nMAIN_HOST=main.example\n' > "$P/.config/localllm/local.env"
cp "$REPO/open-webui/chat-types/example.json" "$P/.config/localllm/chat-types/sample.json"
PL() { HOME="$P" "$REPO/localllm.sh" "$@" 2>&1; }
out="$(PL settings)"
check "split, helper unreachable: 'settings' says so" has "$out" "can't be reached"
out="$(PL chat-types)"
check "split, helper unreachable: 'chat-types' says so" has "$out" "can't be reached"
out="$(HOME="$P" "$REPO/install/chat-types.sh" --only sample 2>&1)"
check "split: install/chat-types.sh builds on the helper (refused while it can't be reached)" has "$out" "can't be reached"
check "the main PC's settings and chat types commands are in the usage" bash -c '[[ "$1" == *"localllm.sh settings"* && "$1" == *"localllm.sh chat-types"* ]]' _ "$(PL help)"
# db_status: what the page shows of Open WebUI, from a database like Open WebUI's.
db="$TMP/webui.db"
python3 - "$db" <<'SQL'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
c.executescript("""create table model(id text, updated_at int); create table knowledge(id text, name text, description text);
create table knowledge_file(knowledge_id text, file_id text); create table user(email text, name text, role text, created_at int);
insert into model values ('chat-sample', 5), ('other-model', 6);
insert into knowledge values ('k1', 'Sample - sources', 'x [chat type: sample] y'), ('k2', 'Mine', 'nothing');
insert into knowledge_file values ('k1', 'f1'), ('k1', 'f2'); insert into user values ('a@example.org', 'A', 'admin', 1);""")
c.commit()
SQL
st="$(python3 -c 'import json, sys; sys.path.insert(0, sys.argv[1]); import chat_types_web as w; print(json.dumps(w.db_status(sys.argv[2]), sort_keys=True))' "$REPO/services" "$db" 2>&1)"
check "db_status: the chat types' presets and Knowledge bases only, and the accounts" test "$st" = \
    '{"knowledge": {"sample": {"files": 2, "name": "Sample - sources"}}, "presets": {"sample": 5}, "users": [{"email": "a@example.org", "name": "A", "role": "admin"}]}'
# The main PC's server for the page (services/chat_types_server.py), on a free port.
port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
HOME="$P" python3 "$REPO/services/chat_types_server.py" "$port" >/dev/null 2>&1 &
srv=$!
for _ in $(seq 1 50); do curl -s -m 1 -o /dev/null "http://127.0.0.1:$port/chat-types" && break; sleep 0.1; done
code() { curl -s -m 30 -o /dev/null -w '%{http_code}' "$@"; }
check "chat types server: the page, on 127.0.0.1" test "$(code "http://127.0.0.1:$port/chat-types")" = 200
check "... refused for another Host (DNS rebinding)" test "$(code -H 'Host: helper.example' "http://127.0.0.1:$port/chat-types")" = 403
check "... a change refused from another site" test "$(code -X POST -H 'Content-Type: application/json' -H 'Origin: http://evil.example' -d '{}' "http://127.0.0.1:$port/chat-types/save")" = 403
data="$(curl -s -m 60 "http://127.0.0.1:$port/chat-types/data")"
check "... the helper unreachable: this PC's files still listed, with the reason" python3 -c '
import json, sys; d = json.loads(sys.argv[1])
assert [t["id"] for t in d["types"]] == ["sample"] and d["status_error"] and not d["types"][0]["preset"]["exists"]' "$data"
check "... /settings points to ./localllm.sh settings" bash -c 'curl -s -m 5 "$1" | grep -q "localllm.sh settings"' _ "http://127.0.0.1:$port/settings"
kill "$srv" 2>/dev/null
check "the helper's gateway page points to the main PC (CHAT_TYPES_ON_MAIN)" \
    grep -q "CHAT_TYPES_ON_MAIN = '1'" "$REPO/windows/common.ps1"
check "the deploy sends only the chat type files, not .deleted/" \
    grep -q "find \"\$CHAT_TYPES_DIR\" -maxdepth 1 -type f -name '\*.json'" "$REPO/lib/helper.sh"

echo "lib/helper.sh push_stage: the deploy sends only what changed (deployed.sha256), through a fake helper"
st="$TMP/stage" rem="$TMP/helper-folder"
mkdir -p "$st/repo/windows" "$st/chat-types" "$rem"
echo a > "$st/repo/windows/a.ps1"; echo b > "$st/repo/b.py"; echo c > "$st/chat-types/c.json"
# The fake helper_ssh keeps the helper's folder in $REM: it answers the deployed.sha256 read, unpacks the tars and
# writes down what it was told to remove.
push() { HOME="$H" REM="$rem" bash -c 'source "$1/localllm.sh"
helper_ssh() { case "$1" in
    *Test-Path*) [[ -f "$REM/deployed.sha256" ]] && cat "$REM/deployed.sha256" ;;
    "tar -xf - -C"*) tar -xf - -C "$REM" ;;
    *Remove-Item*) echo "$1" >> "$REM/removed.log" ;;
    *) cat >/dev/null ;;
esac; }
push_stage "$2"' _ "$REPO" "$st" 2>&1; }
out="$(push)"
check "first deploy: every file is sent and the list recorded" bash -c '[[ "$(grep -c "^  sent" <<< "$1")" == 3 ]] \
    && cmp -s "$2/repo/b.py" "$3/repo/b.py" && test -f "$3/deployed.sha256"' _ "$out" "$st" "$rem"
check "nothing changed: nothing sent" has "$(push)" "The helper is up to date."
echo b2 > "$st/repo/b.py"
out="$(push)"
check "one file changed: only that file is sent" bash -c '[[ "$(grep "^  sent" <<< "$1")" == "  sent    repo/b.py" ]] \
    && grep -qx b2 "$2/repo/b.py"' _ "$out" "$rem"
rm "$st/repo/windows/a.ps1"
echo '0000  data/webui.db' >> "$rem/deployed.sha256"
out="$(push)"
check "a file gone from the repo is removed on the helper, nothing sent" bash -c 'grep -q "removed repo/windows/a.ps1" <<< "$1" \
    && ! grep -q "^  sent" <<< "$1" && grep -qF "repo\\windows\\a.ps1" "$2/removed.log"' _ "$out" "$rem"
check "... and only repo/ and chat-types/ files are ever removed (the helper's data isn't)" \
    bash -c '! grep -q "data" "$1/removed.log"' _ "$rem"

echo "Rebuilding from a split-mode backup (restore.sh in a copy of the repo, temporary HOME)"
R="$TMP/rebuild-repo"
mkdir -p "$R/backup/localllm" "$TMP/rebuild-home"
cp -r "$REPO/lib" "$REPO/config" "$REPO/restore.sh" "$R/"
mkdir -p "$R/open-webui" "$R/backup/open-webui/uploads"
cp "$REPO/open-webui/relocate_uploads.py" "$R/open-webui/"
printf 'LOCALLLM_MODE="split"\nLOCALLLM_APPS=pi\nHELPER_HOST=helper.example\nHELPER_USER=someone\n' > "$R/backup/localllm/local.env"
# Open WebUI's database as the helper keeps it: an upload listed with its path on the helper's data drive.
sqlite3 "$R/backup/open-webui/webui.db" "create table file (id text primary key, path text); insert into file values ('1', 'D:\\LocalLLM\\open-webui\\uploads\\1_doc.pdf');"
echo pdf > "$R/backup/open-webui/uploads/1_doc.pdf"
rout="$(HOME="$TMP/rebuild-home" "$R/restore.sh" --missing-only 2>&1)"
check "a split-mode backup's uploads point at this PC's uploads folder" test \
    "$(sqlite3 "$TMP/rebuild-home/.open-webui/webui.db" 'select path from file')" = "$TMP/rebuild-home/.open-webui/uploads/1_doc.pdf"
check "... and the first start sets Open WebUI's settings for this PC (the marker restore.sh leaves)" \
    test -f "$TMP/rebuild-home/.config/localllm/webui-from-split"
check "... which start/open-webui.sh reads, and switch split clears" bash -c \
    'grep -q WEBUI_FROM_SPLIT_FILE "$1/start/open-webui.sh" && grep -q "rm -f \"\$WEBUI_FROM_SPLIT_FILE\"" "$1/lib/helper.sh"' _ "$REPO"
check "a fresh PC starts in single mode (Open WebUI's data is restored here)" \
    grep -qx 'LOCALLLM_MODE=single' "$TMP/rebuild-home/.config/localllm/local.env"
check "... and re-enables owui when the backup was made in LOCALLLM_APPS=pi" \
    grep -qx 'LOCALLLM_APPS=pi,owui' "$TMP/rebuild-home/.config/localllm/local.env"
check "... the helper's name kept, for setup-helper" grep -qx 'HELPER_HOST=helper.example' "$TMP/rebuild-home/.config/localllm/local.env"
check "... and it says how to get back to split mode" has "$rout" "switch split"
check "split_endpoints includes OWUI services even when LOCALLLM_APPS=pi (install-helper installs full stack)" bash -c '
HOME="$1" bash -c "source \"$2/lib/common.sh\"; source \"$2/lib/helper.sh\"; LOCALLLM_APPS=pi HELPER_HOST=h.example MAIN_HOST=m.example split_endpoints \"$1/se.json\"" >/dev/null 2>&1
python3 -c "import json,sys; d=json.load(open(sys.argv[1]))[\"services\"]; assert d[\"open-webui\"][\"where\"]==\"helper\" and d[\"whisper\"][\"where\"]==\"helper\"" "$1/se.json"' _ "$TMP" "$REPO"
mkdir -p "$TMP/live-home/.config/localllm"
printf 'LOCALLLM_MODE=split\nHELPER_HOST=helper.example\n' > "$TMP/live-home/.config/localllm/local.env"
lout="$(HOME="$TMP/live-home" "$R/restore.sh" --missing-only 2>&1)"
check "a PC that already has its settings keeps its mode" grep -qx 'LOCALLLM_MODE=split' "$TMP/live-home/.config/localllm/local.env"
check "... and, in split mode, it points to restore-helper-data for the data it put here" has "$lout" "restore-helper-data"
printf 'LOCALLLM_MODE=single\n' > "$R/backup/localllm/local.env"
HOME="$TMP/single-home" "$R/restore.sh" --missing-only >/dev/null 2>&1
check "a single-mode backup restores Open WebUI's data without the marker" bash -c \
    '[[ -f "$1/.open-webui/webui.db" && ! -f "$1/.config/localllm/webui-from-split" ]]' _ "$TMP/single-home"
check "switch split asks before replacing data the helper already has" grep -q "Keep the helper's data (h)" "$REPO/lib/helper.sh"
check "the data counts match on both PCs (same report, newest chat included)" bash -c \
    '[[ "$(grep -c "newest_chat\"\] = q(con" "$1/lib/helper.sh" "$1/windows/tasks.ps1" | grep -c ":1$")" == 2 ]]' _ "$REPO"
check "setup-helper offers to forget a reinstalled helper's old fingerprint" grep -q 'REMOTE HOST IDENTIFICATION HAS CHANGED' "$REPO/localllm.sh"

echo "windows/*.ps1"
for f in "$REPO"/windows/*.ps1; do
    check "$(basename -- "$f"): plain ASCII" bash -c '! LC_ALL=C grep -qP "[^\x00-\x7F]" "$1"' _ "$f"
done

echo "windows/bootstrap.ps1"
check "has the key placeholder setup-helper fills in" grep -q "@MAIN_PC_PUBLIC_KEY@" "$REPO/windows/bootstrap.ps1"
check "the filled-in copy is written outside the repo (~/.cache/localllm/)" grep -q '\.cache/localllm/bootstrap\.local\.ps1' "$REPO/localllm.sh"

echo "two-pc-check: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
