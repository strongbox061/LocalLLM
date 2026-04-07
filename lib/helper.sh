#!/usr/bin/env bash
# The main PC's side of the helper PC (README, "Two-PC setup"): deploy, install-helper, switch and gaming, for
# ./localllm.sh. Source it after lib/common.sh, don't run it.
# The variables here are used by localllm.sh.
# shellcheck disable=SC2034

HELPER_LOCALLLM='C:\ProgramData\LocalLLM'
HELPER_WIN='C:\ProgramData\LocalLLM\repo\windows'
# What the deploy sends: the repo files the helper runs (paths relative to the repo).
DEPLOY_PATHS=(windows services open-webui models/helper-small.json config/services.json)
DEPLOY_EXCLUDE='^windows/bootstrap|\.service$|\.timer$|^services/(kokoro|searxng)/|^services/(llm-server|open-webui)\.sh$|__pycache__'

# helper.env (config/helper.example.env): the helper's own values, kept on the main PC (and in the backup).
load_helper_env() {
    if [[ ! -f "$HELPER_ENV_FILE" ]]; then
        mkdir -p "$(dirname -- "$HELPER_ENV_FILE")"
        (umask 077 && cp "$REPO_DIR/config/helper.example.env" "$HELPER_ENV_FILE")
        echo "Made $HELPER_ENV_FILE from config/helper.example.env."
    fi
    # shellcheck source=/dev/null
    source "$HELPER_ENV_FILE"
}

# set_helper_env <NAME> <value>: like set_local_env, in helper.env.
set_helper_env() {
    LOCAL_ENV_FILE="$HELPER_ENV_FILE" set_local_env "$@"
}

helper_task() {     # helper_task <task> [arguments]: windows\tasks.ps1 on the helper
    helper_ps "$HELPER_WIN\\tasks.ps1" "$@"
}

# The helper's Open WebUI must be the main PC's version (an older one can't open a newer database).
main_webui_version() {
    uv tool list 2>/dev/null | sed -nE 's/^open-webui v([0-9][^ ]*).*/\1/p'
}

# pick_data_dir: HELPER_DATA_DIR in helper.env; when unset, the helper's internal NTFS drive with the most free space,
# saved there (so every later run, and a rebuild from the backup, uses the same place).
pick_data_dir() {
    [[ -n "${HELPER_DATA_DIR:-}" ]] && return 0
    local drives letter
    drives="$(helper_task drives 2>/dev/null | tr -d '\r')" || die "Couldn't list the helper's drives."
    letter="$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(d[0]["letter"] if d else "")' "$drives" 2>/dev/null)"
    [[ -n "$letter" ]] || die "The helper has no internal NTFS drive to use ($drives)."
    python3 -c 'import json,sys
for d in json.loads(sys.argv[1]): print(f"  {d[\"letter\"]}: {d[\"free_gb\"]} GB free of {d[\"size_gb\"]} GB")' "$drives" 2>/dev/null \
        || printf '%s\n' "$drives"
    HELPER_DATA_DIR="$letter:\\LocalLLM"
    set_helper_env HELPER_DATA_DIR "$HELPER_DATA_DIR"
    echo "Data folder on the helper: $HELPER_DATA_DIR (the drive with the most free space; saved as HELPER_DATA_DIR in"
    echo "$HELPER_ENV_FILE: edit it there to use another one before the first switch)."
}

# split_endpoints <file>: endpoints.json as split mode has them, also while this PC is still in single mode
# (install-helper runs before switch split). Always includes owui so install-helper installs the full helper stack even
# when LOCALLLM_APPS=pi (the runtime apps toggle stays in config.json's "apps" field).
split_endpoints() {
    local args=(LOCALLLM_MODE=split "LOCALLLM_APPS=pi,owui" HELPER_HOST="${HELPER_HOST:-}" MAIN_HOST="${MAIN_HOST:-}") v
    for v in $(compgen -v PLACE_); do args+=("$v=${!v}"); done
    env "${args[@]}" python3 "$REPO_DIR/lib/endpoints.py" print > "$1" || die "The split-mode addresses don't fit together."
}

# build_stage <dir>: everything the helper gets, laid out as C:\ProgramData\LocalLLM (repo\, config.json, model.json,
# chat-types\, secrets\). Without HELPER_DATA_DIR only the repo files.
build_stage() {
    local stage="$1" f eps
    mkdir -p "$stage/repo"
    while IFS= read -r f; do
        [[ -f "$REPO_DIR/$f" ]] || continue
        mkdir -p "$stage/repo/$(dirname -- "$f")"
        cp "$REPO_DIR/$f" "$stage/repo/$f"
    done < <(cd "$REPO_DIR" && git ls-files --cached --others --exclude-standard -- "${DEPLOY_PATHS[@]}" | grep -Ev "$DEPLOY_EXCLUDE")
    [[ -n "${HELPER_DATA_DIR:-}" ]] || return 0
    eps="$stage/.endpoints.json"
    split_endpoints "$eps"
    write_model_json_quiet
    HELPER_DATA_DIR="$HELPER_DATA_DIR" OPEN_WEBUI_VERSION="$(main_webui_version)" LOCALLLM_APPS="${LOCALLLM_APPS:-pi,owui}" \
        python3 "$REPO_DIR/lib/helper_config.py" "$eps" "$MODEL_JSON_FILE" > "$stage/config.json" || die "helper config.json failed."
    rm -f "$eps"
    cp "$MODEL_JSON_FILE" "$stage/model.json"
    # Only the chat type files themselves (not .deleted/, the page's deleted ones, kept here and in the backup).
    mkdir -p "$stage/chat-types"
    if [[ -d "$CHAT_TYPES_DIR" ]]; then find "$CHAT_TYPES_DIR" -maxdepth 1 -type f -name '*.json' -exec cp {} "$stage/chat-types/" \; ; fi
    mkdir -p "$stage/secrets"
    ensure_llama_api_key >/dev/null
    cp "$LLAMA_API_KEY_FILE" "$stage/secrets/llama-api-key"
    [[ -f "$WEBUI_KEY_FILE" ]] && cp "$WEBUI_KEY_FILE" "$stage/secrets/webui-secret-key"
    return 0
}

write_model_json_quiet() {
    # shellcheck source=lib/model.sh
    source "$REPO_DIR/lib/model.sh"
    load_model_profile >/dev/null && write_model_json >/dev/null
}

# Every tar here is pax: it records file names as UTF-8, which Windows' tar reads right (in its default format a name
# like "Código.md" arrives as "C├│digo.md", read in the console's code page).
DEPLOYED=()     # what the last deploy sent (paths relative to C:\ProgramData\LocalLLM)

# push_stage <dir>: sends what differs from the helper's last deploy (deployed.sha256 there), removes repo files that
# are gone, and records the new list. The folder is made Administrators/SYSTEM-only before anything lands in it.
push_stage() {
    local stage="$1" remote changed removed
    (cd "$stage" && find . -type f ! -name deployed.sha256 -printf '%P\n' | sort | xargs -r -d '\n' sha256sum) > "$stage/deployed.sha256"
    helper_ssh "\$d='$HELPER_LOCALLLM'; New-Item -ItemType Directory -Force -Path \$d | Out-Null; icacls.exe \$d /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' /Q | Out-Null" \
        || die "Couldn't prepare $HELPER_LOCALLLM on the helper."
    remote="$(helper_ssh "if (Test-Path '$HELPER_LOCALLLM\\deployed.sha256') { Get-Content '$HELPER_LOCALLLM\\deployed.sha256' }" | tr -d '\r')"
    changed="$(comm -23 <(sort "$stage/deployed.sha256") <(printf '%s\n' "$remote" | sort) | awk '{ print $2 }')"
    removed="$(comm -13 <(awk '{ print $2 }' "$stage/deployed.sha256" | sort) <(printf '%s\n' "$remote" | awk 'NF { print $2 }' | sort) \
        | grep -E '^(repo|chat-types)/' || true)"
    mapfile -t DEPLOYED < <(printf '%s\n' "$changed" | sed '/^$/d')
    if [[ ${#DEPLOYED[@]} -eq 0 && -z "$removed" ]]; then
        echo "The helper is up to date."
        return 0
    fi
    if [[ ${#DEPLOYED[@]} -gt 0 ]]; then
        (cd "$stage" && tar --format=pax -cf - "${DEPLOYED[@]}") | helper_ssh "tar -xf - -C '$HELPER_LOCALLLM'" \
            || die "Sending the files to the helper failed."
        printf '  sent    %s\n' "${DEPLOYED[@]}"
    fi
    if [[ -n "$removed" ]]; then
        local f
        while IFS= read -r f; do
            helper_ssh "Remove-Item -Force -ErrorAction SilentlyContinue '$HELPER_LOCALLLM\\${f//\//\\}'"
            echo "  removed $f"
        done <<< "$removed"
    fi
    (cd "$stage" && tar -cf - deployed.sha256) | helper_ssh "tar -xf - -C '$HELPER_LOCALLLM'" || die "Recording the deploy failed."
}

# deploy_files [--no-checks]: the checks first (stops on a failure), then the changed files.
deploy_files() {
    load_helper_env    # without HELPER_DATA_DIR the stage would lack chat-types\ and the helper would delete them
    if [[ "${1:-}" != --no-checks ]]; then
        log "Checks before the deploy"
        python3 "$REPO_DIR/lang/build.py" --check >/dev/null || die "lang/phrases.json changed: run python3 lang/build.py first."
        local checks
        checks="$(mktemp)"
        if ! node --experimental-strip-types "$REPO_DIR/tests/guard-tests.mjs" > "$checks" 2>&1; then
            grep -A3 'FAIL' "$checks" | head -20
            die "The guard checks failed (full output: $checks): nothing sent."
        fi
        echo "Guard checks: $(tail -1 "$checks")"
        rm -f "$checks"
    fi
    log "Files to the helper PC"
    local stage
    stage="$(mktemp -d)"
    chmod 700 "$stage"
    build_stage "$stage"
    push_stage "$stage"
    rm -rf "$stage"
}

# apply_deployed: what the sent files change on the helper, when it runs.
apply_deployed() {
    [[ ${#DEPLOYED[@]} -gt 0 ]] || return 0
    local f restart_gateway="" restart_router="" restart_whisper="" loader="" sync="" config="" chat=""
    for f in "${DEPLOYED[@]}"; do
        case "$f" in
            repo/services/search_gateway* | repo/services/chat_types* | repo/services/search-gateway-requirements.txt) restart_gateway=1 ;;
            repo/services/voice_router.py) restart_router=1 ;;
            repo/services/whisper_server.py) restart_whisper=1 ;;
            repo/open-webui/loader.js | repo/open-webui/install_loader.py) loader=1 ;;
            repo/open-webui/tools/* | repo/open-webui/functions/* | repo/open-webui/sync.py) sync=1 ;;
            chat-types/*) chat=1 ;;
            config.json | secrets/* | repo/windows/run-service.ps1 | repo/windows/common.ps1) config=1 ;;
        esac
    done
    log "Applying on the helper"
    [[ -n "$restart_gateway" ]] && helper_ps "$HELPER_WIN\\services.ps1" restart search-gateway
    [[ -n "$restart_router" ]] && helper_ps "$HELPER_WIN\\services.ps1" restart voice-router
    [[ -n "$restart_whisper" ]] && helper_ps "$HELPER_WIN\\services.ps1" restart whisper
    # The voice fixes: in place at once (a reload of Open WebUI's page uses them).
    [[ -n "$loader" ]] && helper_task loader
    if [[ -n "$sync$chat" ]] && helper_ps "$HELPER_WIN\\services.ps1" status open-webui -Json 2>/dev/null | grep -q '"running"'; then
        [[ -n "$sync" ]] && helper_task sync
        [[ -n "$chat" ]] && helper_task chat-types
    elif [[ -n "$sync$chat" ]]; then
        echo "Open WebUI isn't running on the helper: its tools and chat types are pushed when it next starts (./localllm.sh deploy)."
    fi
    [[ -n "$config" ]] && echo "The helper's settings changed: ./localllm.sh restart helper uses them."
    return 0
}

# run_setup [steps]: windows\setup.ps1 on the helper, with restarts when it asks for one (exit 3), at most 3.
run_setup() {
    local tries=0 code
    while :; do
        log "Setup on the helper PC${1:+ ($1)}"
        if [[ -n "${1:-}" ]]; then helper_ps "$HELPER_WIN\\setup.ps1" -Only "$1"; else helper_ps "$HELPER_WIN\\setup.ps1"; fi
        code=$?
        case "$code" in
            0) return 0 ;;
            3)
                tries=$((tries + 1))
                [[ $tries -le 3 ]] || die "The helper asked for a restart three times: look at its screen."
                restart_helper_and_wait || return 1
                ;;
            4) die "An installer on the helper looks stuck (see above). Click away its dialog there, then run ./localllm.sh install-helper again." ;;
            *) die "Setup on the helper failed (exit $code; see above). Fix it, then run ./localllm.sh install-helper again (finished steps are skipped)." ;;
        esac
    done
}

# restart_helper_and_wait: restarts the helper PC on purpose, then waits in stages (each with a time limit): Tailscale
# answers, the SSH port opens, an SSH command runs.
restart_helper_and_wait() {
    log "Restarting the helper PC (Windows asked for it)"
    helper_ssh 'Restart-Computer -Force' >/dev/null 2>&1 || true
    sleep 30
    wait_stage "Tailscale" 600 "tailscale ping -c 1 --timeout 5s '$HELPER_HOST'" || return 1
    wait_stage "SSH port" 300 "timeout 5 bash -c 'exec 3<>/dev/tcp/$HELPER_HOST/22'" || return 1
    wait_stage "SSH login" 300 "helper_reachable" || return 1
}

wait_stage() {     # wait_stage <label> <seconds> <command>
    local t0=$SECONDS
    printf '  waiting for %s' "$1"
    while (( SECONDS - t0 < $2 )); do
        if eval "$3" >/dev/null 2>&1; then echo " ok"; return 0; fi
        printf '.'
        sleep 10
    done
    echo
    warn "The helper isn't back after $(( $2 / 60 )) minutes ($1): look at its screen (a Windows update screen? a sign-in?), then run the command again."
    return 1
}

do_install_helper() {     # do_install_helper [step,step...] (setup.ps1's -Only)
    require_helper_connection
    load_helper_env
    ensure_llama_api_key
    deploy_files
    pick_data_dir
    # Again with the data folder known: config.json and the keys.
    deploy_files --no-checks
    run_setup "${1:-}"
    log "The helper's services"
    helper_ps "$HELPER_WIN\\services.ps1" status
    echo
    if [[ -n "${1:-}" ]]; then
        echo "Done: $1."
    elif [[ "$LOCALLLM_MODE" == split ]]; then
        echo "Done. ./localllm.sh restart helper uses what changed."
    else
        echo "Installed. Next: ./localllm.sh switch split (moves Open WebUI's data there and starts everything)."
    fi
}

helper_unreachable_message() {
    warn "The helper PC ($HELPER_HOST) can't be reached over SSH: is it on, and Tailscale running on both PCs?"
    warn "  Its own services can still be started on it (desktop shortcut \"Start AI services\")."
    warn "  Test the connection: ./localllm.sh setup-helper. Everything on this PC instead: ./localllm.sh switch single"
}

require_helper_connection() {
    [[ -n "${HELPER_HOST:-}" ]] || die "No HELPER_HOST yet: run ./localllm.sh setup-helper first."
    helper_reachable || { helper_unreachable_message; die "The helper PC can't be reached."; }
}

# --- switch: moving Open WebUI's data between the PCs ---

# local_counts <dir>: the same report as tasks.ps1 check, for a folder on this PC (open-webui/, search-gateway/).
local_counts() {
    python3 - "$1" <<'PY'
import hashlib, json, os, sqlite3, sys
d = sys.argv[1]
out = {}
def q(con, sql):
    try: return con.execute(sql).fetchone()[0]
    except sqlite3.Error: return None
for name, rel in (("webui", "open-webui/webui.db"), ("chroma", "open-webui/vector_db/chroma.sqlite3"), ("gateway", "search-gateway/search-gateway.db")):
    p = os.path.join(d, *rel.split("/"))
    if not os.path.exists(p): out[name] = None; continue
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    out[name] = {"integrity": q(con, "pragma integrity_check")}
    if name == "webui":
        for t in ("chat", "user", "file", "knowledge", "model"): out[name][t] = q(con, f"select count(*) from {t}")
        out[name]["newest_chat"] = q(con, "select max(updated_at) from chat")
    con.close()
up = os.path.join(d, "open-webui", "uploads")
names = sorted(os.path.relpath(os.path.join(r, f), up).replace(os.sep, "/") for r, _, fs in os.walk(up) for f in fs) if os.path.isdir(up) else []
out["uploads"] = len(names)
out["upload_names"] = hashlib.sha256("\n".join(names).encode("utf-8")).hexdigest()[:16]
print(json.dumps(out, sort_keys=True))
PY
}

# data_summary <json>: one line for people, from a counts report.
data_summary() {
    python3 -c '
import json, sys, datetime
w = (json.loads(sys.argv[1]) or {}).get("webui") or {}
t = w.get("newest_chat")
when = datetime.datetime.fromtimestamp(t if t < 1e11 else t / 1e9).strftime("%Y-%m-%d %H:%M") if t else "none"
print("%s chats, %s users, %s Knowledge bases, newest chat %s" % (w.get("chat"), w.get("user"), w.get("knowledge"), when))
' "$1" 2>/dev/null || echo "$1"
}

# counts_ok <json>: every database there says ok.
counts_ok() {
    python3 -c 'import json,sys; d=json.loads(sys.argv[1]); sys.exit(0 if all(v is None or v.get("integrity")=="ok" for k,v in d.items() if isinstance(v,dict)) and d.get("webui") else 1)' "$1"
}

# snapshot_main <dir>: Open WebUI's and the search gateway's data on this PC, consistent (both stopped), as
# <dir>/open-webui/{webui.db,uploads,vector_db} and <dir>/search-gateway/{search-gateway.db,search-gateway.json}.
snapshot_main() {
    local d="$1" item
    mkdir -p "$d/open-webui" "$d/search-gateway"
    sqlite3 "$WEBUI_DATA_DIR/webui.db" ".backup '$d/open-webui/webui.db'"
    [[ -d "$WEBUI_DATA_DIR/uploads" ]] && cp -r "$WEBUI_DATA_DIR/uploads" "$d/open-webui/uploads"
    if [[ -d "$WEBUI_DATA_DIR/vector_db" ]]; then
        mkdir -p "$d/open-webui/vector_db"
        sqlite3 "$WEBUI_DATA_DIR/vector_db/chroma.sqlite3" ".backup '$d/open-webui/vector_db/chroma.sqlite3'"
        for item in "$WEBUI_DATA_DIR"/vector_db/*; do
            case "$(basename -- "$item")" in chroma.sqlite3*) continue ;; esac
            cp -r "$item" "$d/open-webui/vector_db/"
        done
    fi
    for item in "$d/open-webui/webui.db" "$d/open-webui/vector_db/chroma.sqlite3"; do
        [[ -f "$item" ]] && sqlite3 "$item" 'pragma journal_mode=delete;' >/dev/null
    done
    [[ -f "$SEARCH_GATEWAY_DB" ]] && sqlite3 "$SEARCH_GATEWAY_DB" ".backup '$d/search-gateway/search-gateway.db'"
    [[ -f "$SEARCH_GATEWAY_SETTINGS" ]] && cp "$SEARCH_GATEWAY_SETTINGS" "$d/search-gateway/search-gateway.json"
    return 0
}

do_switch() {
    local to="$1"
    [[ "$to" == single || "$to" == split ]] || die "Usage: ./localllm.sh switch single|split"
    if [[ "$LOCALLLM_MODE" == "$to" ]]; then
        echo "Already in $to mode."
        return 0
    fi
    apps_has owui \
        || die "Open WebUI is off (LOCALLLM_APPS=$LOCALLLM_APPS): run ./localllm.sh apps pi,owui before switching PCs."
    require_helper_connection
    load_helper_env
    require_same_webui_version
    if [[ "$to" == split ]]; then switch_to_split; else switch_to_single; fi
}

# The data moves only between the same Open WebUI versions (an older one can't open a newer database).
require_same_webui_version() {
    local here there
    here="$(main_webui_version)"
    there="$(helper_task version 2>/dev/null | tr -d '\r' | tail -1)"
    [[ -n "$there" ]] || die "Open WebUI isn't installed on the helper: run ./localllm.sh install-helper first."
    [[ "$here" == "$there" ]] || die "Open WebUI's versions differ (here $here, helper $there): an older one can't open a newer database. Run ./localllm.sh install-helper (it installs this PC's version there), then try again."
}

# --- restore-helper-data: a reinstalled helper PC gets Open WebUI's data back from the backup ---
# In split mode the only copy of Open WebUI's and the search gateway's data is on the helper; after Windows was
# reinstalled there (and setup-helper and install-helper were run again), the newest copy is the last backup's, in
# backup/ on this PC (the backup timer refreshes it every 6 hours; an older archive can be unpacked there first, README
# "Opening a backup"). This puts that copy on this PC, in single mode, and then moves it to the helper exactly like
# `switch split` (checks on both sides, the helper's settings, this PC's copy retired).
do_restore_helper_data() {
    [[ "$LOCALLLM_MODE" == split ]] \
        || die "This PC is in single mode: Open WebUI's data is here, not on the helper. ./localllm.sh switch split moves it there."
    require_helper_connection
    load_helper_env
    require_same_webui_version
    [[ -f "$BACKUP_DIR/open-webui/webui.db" ]] \
        || die "No Open WebUI data in $BACKUP_DIR: unpack the newest backup archive there first (README, \"Opening a backup\")."

    local stamp stage counts theirs answer
    stamp="$(date +%Y%m%d-%H%M%S)"
    log "1/4 What the backup holds, and what the helper has"
    stage="$(mktemp -d)"
    chmod 700 "$stage"
    mkdir -p "$stage/open-webui" "$stage/search-gateway"
    cp "$BACKUP_DIR/open-webui/webui.db" "$stage/open-webui/"
    [[ -f "$BACKUP_DIR/search-gateway/search-gateway.db" ]] && cp "$BACKUP_DIR/search-gateway/search-gateway.db" "$stage/search-gateway/"
    [[ -f "$BACKUP_DIR/open-webui/vector_db/chroma.sqlite3" ]] && mkdir -p "$stage/open-webui/vector_db" \
        && cp "$BACKUP_DIR/open-webui/vector_db/chroma.sqlite3" "$stage/open-webui/vector_db/"
    [[ -d "$BACKUP_DIR/open-webui/uploads" ]] && cp -r "$BACKUP_DIR/open-webui/uploads" "$stage/open-webui/"
    counts="$(local_counts "$stage")"
    rm -rf "$stage"
    counts_ok "$counts" || die "The backup's data failed its integrity check ($counts): nothing changed."
    echo "  backup ($(date -r "$BACKUP_DIR/open-webui/webui.db" '+%Y-%m-%d %H:%M')): $(data_summary "$counts")"
    theirs="$(helper_task check "$HELPER_DATA_DIR" 2>/dev/null | tr -d '\r' | tail -1)"
    if python3 -c 'import json,sys; sys.exit(0 if (json.loads(sys.argv[1]) or {}).get("webui") else 1)' "$theirs" 2>/dev/null; then
        echo "  helper: $(data_summary "$theirs")"
        echo "The helper still has Open WebUI data. If it is the right one, nothing needs restoring (./localllm.sh start)."
        echo "Otherwise the next step asks again whether to replace it (the helper's is kept as ...-before-<date>)."
    else
        echo "  helper: no Open WebUI data (a reinstalled helper)"
    fi
    [[ -t 0 ]] || die "Run ./localllm.sh restore-helper-data in a terminal: it asks before changing anything."
    answer="$(ask "Put the backup's data on the helper? (yes/no)" "no")"
    [[ "$answer" == yes ]] || die "Nothing changed."

    log "2/4 Stopping the helper's services"
    helper_ps "$HELPER_WIN\\services.ps1" stop all >/dev/null || true

    log "3/4 The backup's data on this PC (anything here before kept as ...-before-$stamp)"
    if [[ -d "$WEBUI_DATA_DIR" ]] && [[ -n "$(ls -A "$WEBUI_DATA_DIR")" ]]; then
        mv "$WEBUI_DATA_DIR" "$WEBUI_DATA_DIR-before-$stamp"
        echo "  $WEBUI_DATA_DIR -> $(basename -- "$WEBUI_DATA_DIR")-before-$stamp"
    fi
    rm -rf "$WEBUI_DATA_DIR"
    mkdir -p "$WEBUI_DATA_DIR"
    cp "$BACKUP_DIR/open-webui/webui.db" "$WEBUI_DATA_DIR/webui.db"
    local item
    for item in uploads vector_db; do
        [[ -d "$BACKUP_DIR/open-webui/$item" ]] && cp -r "$BACKUP_DIR/open-webui/$item" "$WEBUI_DATA_DIR/$item"
    done
    mkdir -p "$(dirname -- "$SEARCH_GATEWAY_DB")"
    if [[ -f "$BACKUP_DIR/search-gateway/search-gateway.db" ]]; then
        [[ -f "$SEARCH_GATEWAY_DB" ]] && mv "$SEARCH_GATEWAY_DB" "$SEARCH_GATEWAY_DB.before-$stamp"
        rm -f "$SEARCH_GATEWAY_DB-wal" "$SEARCH_GATEWAY_DB-shm"
        cp "$BACKUP_DIR/search-gateway/search-gateway.db" "$SEARCH_GATEWAY_DB"
    fi
    # The helper's search settings as the last backup saved them (API keys, order, limits); switch split sends them.
    if [[ -f "$BACKUP_DIR/localllm/search-gateway.json" ]]; then
        [[ -f "$SEARCH_GATEWAY_SETTINGS" ]] && cp -a "$SEARCH_GATEWAY_SETTINGS" "$SEARCH_GATEWAY_SETTINGS.before-$stamp"
        (umask 077 && cp "$BACKUP_DIR/localllm/search-gateway.json" "$SEARCH_GATEWAY_SETTINGS")
    fi
    make_private
    python3 "$REPO_DIR/open-webui/relocate_uploads.py" "$WEBUI_DATA_DIR/webui.db" "$WEBUI_DATA_DIR/uploads" \
        || die "The uploads' paths couldn't be set (see above); the data is on this PC, nothing sent."
    # If the move below stops halfway, this PC is left in single mode with the data: the next start/open-webui.sh
    # sets Open WebUI's settings for it (they are the helper's in a split-mode backup).
    : > "$WEBUI_FROM_SPLIT_FILE"
    set_local_env LOCALLLM_MODE single
    LOCALLLM_MODE="single"
    refresh_endpoints force || die "$ENDPOINTS_ERROR"
    # shellcheck source=/dev/null
    source "$ENDPOINTS_ENV"
    echo "  in place; this PC is in single mode until the move below ends"

    log "4/4 Moving it to the helper (as ./localllm.sh switch split)"
    switch_to_split
}

switch_to_split() {
    local stamp stage counts there_counts incoming
    stamp="$(date +%Y%m%d-%H%M%S)"
    log "1/6 Stopping Open WebUI and the search gateway on both PCs"
    run_main stop open-webui
    run_main stop search-gateway
    [[ "${WHERE_KOKORO:-main}" == main ]] && run_main stop kokoro
    FAILED=()
    helper_ps "$HELPER_WIN\\services.ps1" stop all >/dev/null || true
    webui_running && die "Open WebUI is still running here: stop it, then switch again."

    log "2/6 A consistent copy of the data here"
    stage="$(mktemp -d)"
    chmod 700 "$stage"
    snapshot_main "$stage"
    counts="$(local_counts "$stage")"
    counts_ok "$counts" || { rm -rf "$stage"; do_start all >/dev/null; die "The copy here failed its integrity check ($counts): nothing moved."; }
    echo "  $counts"

    # The helper still holding Open WebUI's data in single mode means this PC was rebuilt from a backup while the
    # helper kept its own (newer, unless its disk was older): ask which one to keep.
    local theirs keep=main
    theirs="$(helper_task check "$HELPER_DATA_DIR" 2>/dev/null | tr -d '\r' | tail -1)"
    if python3 -c 'import json,sys; sys.exit(0 if (json.loads(sys.argv[1]) or {}).get("webui") else 1)' "$theirs" 2>/dev/null; then
        echo
        echo "The helper already has Open WebUI data (after rebuilding only this PC, the helper's is the newer one):"
        echo "  this PC: $(data_summary "$counts")"
        echo "  helper:  $(data_summary "$theirs")"
        [[ -t 0 ]] || { rm -rf "$stage"; die "Run ./localllm.sh switch split in a terminal to choose: nothing moved."; }
        case "$(ask "Keep the helper's data (h) or replace it with this PC's (m; the helper's kept as ...-before-$stamp)" "")" in
            h | H) keep=helper ;;
            m | M) keep=main ;;
            *) rm -rf "$stage"; do_start all >/dev/null; die "No choice made: nothing moved." ;;
        esac
        if [[ "$keep" == helper ]]; then
            counts_ok "$theirs" || { rm -rf "$stage"; do_start all >/dev/null; die "The helper's data failed its integrity check ($theirs): nothing moved."; }
        fi
    fi

    if [[ "$keep" == helper ]]; then
        log "3/6 Keeping the helper's data (nothing sent)"
        rm -rf "$stage"
    else
        log "3/6 Sending it to the helper and checking it there"
        incoming="$HELPER_DATA_DIR\\incoming-$stamp"
        (cd "$stage" && tar --format=pax -cf - open-webui search-gateway) \
            | helper_ssh "New-Item -ItemType Directory -Force -Path '$incoming' | Out-Null; tar -xf - -C '$incoming'" \
            || { rm -rf "$stage"; die "Sending the data failed: nothing moved (this PC's data is untouched; ./localllm.sh start)."; }
        there_counts="$(helper_task check "$incoming" | tr -d '\r' | tail -1)"
        if [[ "$there_counts" != "$counts" ]]; then
            helper_ssh "Remove-Item -Recurse -Force '$incoming'" || true
            rm -rf "$stage"
            die "The copy on the helper differs ($there_counts vs $counts): nothing moved (this PC's data is untouched)."
        fi
        echo "  same counts and integrity on the helper."
        helper_task install-data "$incoming" || die "Putting the data in place on the helper failed (this PC's data is untouched)."
        rm -rf "$stage"
    fi

    log "4/6 This PC in split mode"
    set_local_env LOCALLLM_MODE split
    LOCALLLM_MODE="split"
    refresh_endpoints force || die "$ENDPOINTS_ERROR"
    # shellcheck source=/dev/null
    source "$ENDPOINTS_ENV"
    ensure_llama_api_key
    "$REPO_DIR/install/pi-models.sh"
    deploy_files --no-checks
    # llama-server now asks for the key (Pi has it; the helper's Open WebUI gets it).
    sudo systemctl restart localllm-llm
    wait_for "$LLAMA_URL/health" 300 || warn "llama-server didn't come back: journalctl -u localllm-llm -e"
    expose_main_ports
    "$REPO_DIR/install/browser-address.sh" || warn "Browser autoplay policy wasn't updated: run install/browser-address.sh."

    log "5/6 Starting the helper's services, and Open WebUI's settings for the helper"
    helper_ps "$HELPER_WIN\\services.ps1" start all || warn "Not every service on the helper started (see above)."
    helper_task topology split || warn "Some of Open WebUI's settings weren't set: run ./localllm.sh switch split again later."
    helper_task sync || warn "sync.py on the helper failed (tools, functions, bge-m3 search)."
    [[ -d "$CHAT_TYPES_DIR" ]] && { helper_task chat-types || warn "The chat types weren't pushed."; }

    log "6/6 Retiring this PC's copy (so it can't be used by mistake)"
    retire_main_data "$stamp"
    echo
    echo "Split mode. Open WebUI: $WEBUI_URL   (./localllm.sh status shows everything)"
}

retire_main_data() {
    local stamp="$1"
    mv "$WEBUI_DATA_DIR" "$WEBUI_DATA_DIR-moved-$stamp"
    rm -f "$WEBUI_FROM_SPLIT_FILE"   # the data is the helper's now, with the helper's settings (topology split)
    [[ -f "$SEARCH_GATEWAY_DB" ]] && mv "$SEARCH_GATEWAY_DB" "$SEARCH_GATEWAY_DB.moved-$stamp"
    rm -f "$SEARCH_GATEWAY_DB-wal" "$SEARCH_GATEWAY_DB-shm"
    echo "  $WEBUI_DATA_DIR -> $(basename -- "$WEBUI_DATA_DIR")-moved-$stamp"
    echo "  $SEARCH_GATEWAY_DB -> $(basename -- "$SEARCH_GATEWAY_DB").moved-$stamp"
    echo "  (The search settings, $SEARCH_GATEWAY_SETTINGS, stay here; the helper has its own copy now.)"
}

switch_to_single() {
    local stamp stage counts there_counts outgoing
    stamp="$(date +%Y%m%d-%H%M%S)"
    outgoing="$HELPER_DATA_DIR\\outgoing-$stamp"
    log "1/5 Stopping the helper's services and copying its data"
    helper_ps "$HELPER_WIN\\services.ps1" stop all || die "Couldn't stop the helper's services."
    helper_task snapshot "$outgoing" || die "The copy on the helper failed: nothing moved."
    there_counts="$(helper_task check "$outgoing" | tr -d '\r' | tail -1)"
    counts_ok "$there_counts" || die "The helper's copy failed its integrity check ($there_counts): nothing moved."

    log "2/5 Fetching it and checking it here"
    stage="$(mktemp -d)"
    chmod 700 "$stage"
    helper_ssh "tar --format=pax -cf - -C '$outgoing' open-webui search-gateway" | tar -xf - -C "$stage" \
        || { rm -rf "$stage"; die "Fetching the data failed: nothing moved (./localllm.sh start helper)."; }
    counts="$(local_counts "$stage")"
    if [[ "$counts" != "$there_counts" ]]; then
        rm -rf "$stage"
        die "The copy here differs ($counts vs $there_counts): nothing moved."
    fi
    helper_ssh "Remove-Item -Recurse -Force '$outgoing'" || true
    echo "  $counts"

    log "3/5 Putting it in place (this PC's previous copy kept as ...-before-$stamp)"
    if [[ -d "$WEBUI_DATA_DIR" ]] && [[ -n "$(ls -A "$WEBUI_DATA_DIR")" ]]; then mv "$WEBUI_DATA_DIR" "$WEBUI_DATA_DIR-before-$stamp"; fi
    rm -rf "$WEBUI_DATA_DIR"
    mv "$stage/open-webui" "$WEBUI_DATA_DIR"
    mkdir -p "$(dirname -- "$SEARCH_GATEWAY_DB")"
    [[ -f "$SEARCH_GATEWAY_DB" ]] && mv "$SEARCH_GATEWAY_DB" "$SEARCH_GATEWAY_DB.before-$stamp"
    [[ -f "$stage/search-gateway/search-gateway.db" ]] && mv "$stage/search-gateway/search-gateway.db" "$SEARCH_GATEWAY_DB"
    if [[ -f "$stage/search-gateway/search-gateway.json" ]]; then
        (umask 077 && cp "$stage/search-gateway/search-gateway.json" "$SEARCH_GATEWAY_SETTINGS")
    fi
    rm -rf "$stage"
    make_private
    # Open WebUI lists its uploads with their full path on the helper: point them here.
    python3 "$REPO_DIR/open-webui/relocate_uploads.py" "$WEBUI_DATA_DIR/webui.db" "$WEBUI_DATA_DIR/uploads" \
        || die "The uploads' paths couldn't be set (see above); the data is in place, Open WebUI not started."
    helper_task retire-data || warn "The helper's copy wasn't renamed: do it there (or run this again)."

    log "4/5 This PC in single mode"
    set_local_env LOCALLLM_MODE single
    LOCALLLM_MODE="single"
    refresh_endpoints force || die "$ENDPOINTS_ERROR"
    # shellcheck source=/dev/null
    source "$ENDPOINTS_ENV"
    "$REPO_DIR/install/pi-models.sh"
    sudo systemctl restart localllm-llm
    wait_for "$LLAMA_URL/health" 300 || warn "llama-server didn't come back: journalctl -u localllm-llm -e"
    tailscale serve --tcp 8081 off >/dev/null 2>&1 || true
    tailscale serve --tcp 8080 off >/dev/null 2>&1 || true
    "$REPO_DIR/install/browser-address.sh" || warn "Browser autoplay policy wasn't updated: run install/browser-address.sh."

    log "5/5 Starting everything here, and Open WebUI's settings for this PC"
    rm -f "$WEBUI_FROM_SPLIT_FILE"   # set just below; start/open-webui.sh needn't do it again
    do_start all
    webui_settings_single || true
    echo
    echo "Single mode: everything on this PC again. Open WebUI: $WEBUI_URL"
}
