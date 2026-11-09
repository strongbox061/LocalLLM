#!/usr/bin/env bash
# One command for the whole setup, on one PC (single mode) or on the main PC and the helper PC (split mode; README,
# "Two-PC setup"). The mode and the helper's name come from ~/.config/localllm/local.env (LOCALLLM_MODE, HELPER_HOST,
# HELPER_USER); where each service runs and its address from config/services.json (lib/endpoints.py).
#
#   ./localllm.sh start|stop|restart|status            every service on both PCs (start.sh and stop.sh run these)
#   ./localllm.sh start|stop|restart|status main       only the main PC's
#   ./localllm.sh start|stop|restart|status helper     only the helper PC's (windows\services.ps1 there, over SSH)
#   ./localllm.sh start|stop|restart|status <service>  one service, wherever it runs (names: ./localllm.sh status)
#   ./localllm.sh endpoints                            writes endpoints.json again and shows where everything runs
#   ./localllm.sh setup-helper                         the SSH connection to the helper PC (README, "Connecting the
#                                                      helper PC"); safe to run again
#   ./localllm.sh install-helper [step,step]           installs (or updates) everything on the helper PC; safe to run
#                                                      again (steps: windows/setup.ps1)
#   ./localllm.sh deploy                               sends the repo's changed files and the helper's settings to
#                                                      it, after the guard checks, and applies them
#   ./localllm.sh switch single|split                  moves Open WebUI's data (and the search gateway's) to the
#                                                      other PC, with checks, and runs everything there
#   ./localllm.sh apps [pi|pi,owui]                    shows or switches which apps run (pi = Pi only: 1 full slot
#                                                      with 2x context; pi,owui = Pi + Open WebUI + its tools)
#   ./localllm.sh restore-helper-data                  a reinstalled helper PC: puts Open WebUI's data from backup/
#                                                      back on it, with checks (README, "Rebuilding only the helper
#                                                      PC")
#   ./localllm.sh gaming on|off|status                 the helper's gaming mode (frees its GPU for a game)
#   ./localllm.sh settings                             the search settings page: in split mode it opens an SSH
#                                                      tunnel to the helper's and waits (Ctrl+C closes it)
#   ./localllm.sh chat-types                           the chat types page: in split mode it serves it here, from
#                                                      this PC's files, and waits (Ctrl+C ends it)
#   ./localllm.sh monitor [port]                       web monitor for CPU, GPU and RAM on main and helper PCs, plus
#                                                      single/split and apps mode (default port 8095; Ctrl+C ends it)
#
# Order: start = the main PC's services first (llama-server first), then the helper's; stop = the helper's first,
# then the main PC's, llama-server last. SearXNG keeps running on stop (it starts at boot); `stop searxng` stops it.
# When the helper can't be reached, the main PC's part still runs and the helper is reported as unreachable.
set -uo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/lib/common.sh"
# shellcheck source=lib/helper.sh
source "$REPO_DIR/lib/helper.sh"

# The main PC's services, in start order; stop goes the other way round and leaves SearXNG running. Each has
# start/<name>.sh and stop/<name>.sh (the voice router is part of kokoro's).
MAIN_START=(llama searxng search-gateway open-webui kokoro plannotator)
MAIN_STOP=(plannotator search-gateway kokoro open-webui llama)
HELPER_SERVICES_PS1="$HELPER_REPO\\windows\\services.ps1"

usage() {
    sed -n '2,/^set -uo/p' "${BASH_SOURCE[0]}" | grep '^#' | sed -E 's/^# ?//'
    exit 2
}

# The services config/services.json lists, in its order.
all_services() {
    python3 -c 'import json,sys; [print(s["name"]) for s in json.load(open(sys.argv[1]))["services"]]' "$SERVICES_FILE"
}

# where <service>: main, helper, or empty (doesn't run in this mode).
where() {
    local var="WHERE_${1//-/_}"
    var="${var^^}"
    printf '%s' "${!var:-}"
}

# The script name on the main PC for a service (the voice router starts and stops with Kokoro).
main_script() {
    case "$1" in voice-router) echo kokoro ;; *) echo "$1" ;; esac
}

FAILED=()

run_main() {    # run_main start|stop <service>
    local script
    script="$(main_script "$2")"
    if [[ ! -x "$REPO_DIR/$1/$script.sh" ]]; then
        warn "$2 has no $1/$script.sh on the main PC."
        FAILED+=("$2")
        return 1
    fi
    "$REPO_DIR/$1/$script.sh" || { FAILED+=("$2"); return 1; }
}

HELPER_STATE=""   # "", ok or unreachable (checked once per run)
helper_ok() {
    if [[ -z "$HELPER_STATE" ]]; then
        if helper_reachable; then HELPER_STATE=ok; else HELPER_STATE=unreachable; fi
    fi
    [[ "$HELPER_STATE" == ok ]]
}

run_helper() {  # run_helper start|stop|restart|status <service|all> [more arguments]
    log "Helper PC ($HELPER_HOST): $1 ${2:-all}"
    if ! helper_ok; then
        helper_unreachable_message
        FAILED+=("helper PC")
        return 1
    fi
    helper_ps "$HELPER_SERVICES_PS1" "$@" || { FAILED+=("helper PC: $1 ${2:-all}"); return 1; }
}

require_split() {
    [[ "$LOCALLLM_MODE" == split ]] && return 0
    die "This PC is in single mode (LOCALLLM_MODE in $LOCAL_ENV_FILE): there is no helper PC. README, \"Two-PC setup\"."
}

# In split mode the helper PC reaches the main PC's llama-server and SearXNG over Tailscale: `tailscale serve`
# forwards their ports from the Tailscale address to 127.0.0.1 (both keep listening on 127.0.0.1 only; nothing is
# opened on the LAN). Kept by tailscaled across restarts; set again here when missing.
TAILNET_PORTS=(llama searxng)
expose_main_ports() {
    local s port status
    status="$(tailscale serve status --json 2>/dev/null || echo '{}')"
    for s in "${TAILNET_PORTS[@]}"; do
        [[ "$(where "$s")" == main ]] || continue
        port="$(service_field "$s" port)"
        if python3 -c 'import json,sys; sys.exit(0 if sys.argv[2] in (json.loads(sys.argv[1]).get("TCP") or {}) else 1)' \
            "$status" "$port"; then
            continue
        fi
        tailscale serve --bg --tcp "$port" "tcp://127.0.0.1:$port" >/dev/null \
            || { warn "tailscale serve for port $port failed (run install/tailscale.sh once)."; FAILED+=("$s over Tailscale"); continue; }
        echo "Tailscale: port $port ($s) reachable from the helper PC."
    done
}

service_field() {   # service_field <service> <field> (from endpoints.json)
    python3 -c 'import json,sys; v=json.load(open(sys.argv[1]))["services"][sys.argv[2]][sys.argv[3]]; print("" if v is None else v)' \
        "$ENDPOINTS_FILE" "$1" "$2"
}

do_start() {    # do_start all|main|helper|<service>
    local target="$1" s
    case "$target" in
        all | main)
            for s in "${MAIN_START[@]}"; do
                [[ "$(where "$s")" == main ]] && run_main start "$s"
            done
            [[ "$LOCALLLM_MODE" == split ]] && expose_main_ports
            [[ "$LOCALLLM_MODE" == split && "$target" == all ]] && run_helper start all
            ;;
        helper) require_split; run_helper start all ;;
        *)
            case "$(where "$target")" in
                main) run_main start "$target"; [[ "$LOCALLLM_MODE" == split ]] && expose_main_ports ;;
                helper) run_helper start "$target" ;;
                *) die "$target doesn't run in $LOCALLLM_MODE mode (./localllm.sh status lists what does)." ;;
            esac
            ;;
    esac
    return 0
}

do_stop() {     # do_stop all|main|helper|<service>
    local target="$1" s
    case "$target" in
        all | main)
            [[ "$LOCALLLM_MODE" == split && "$target" == all ]] && run_helper stop all
            for s in "${MAIN_STOP[@]}"; do
                [[ "$(where "$s")" == main ]] && run_main stop "$s"
            done
            ;;
        helper) require_split; run_helper stop all ;;
        *)
            case "$(where "$target")" in
                main) run_main stop "$target" ;;
                helper) run_helper stop "$target" ;;
                *) die "$target doesn't run in $LOCALLLM_MODE mode (./localllm.sh status lists what does)." ;;
            esac
            ;;
    esac
    return 0
}

# answers <url> <health path>: does the service answer?
answers() {
    curl -fsS -m 3 -o /dev/null "$1$2" 2>/dev/null
}

main_state() {  # main_state <service>: running / stopped, for a service on this PC
    local s="$1" url health
    if [[ "$s" == plannotator ]]; then
        local p
        p="$(command -v plannotator || echo "$HOME/.local/bin/plannotator")"
        if [[ -x "$p" ]] && "$p" inbox status --json 2>/dev/null | grep -q '"running": true'; then echo running; else echo stopped; fi
        return
    fi
    url="$(service_field "$s" url)" health="$(service_field "$s" health)"
    if answers "$url" "$health"; then echo running; else echo stopped; fi
}

# The helper's own report (windows\services.ps1 status -Json): one "name<TAB>state" line per service.
HELPER_REPORT=""
helper_report() {
    [[ -n "$HELPER_REPORT" ]] && return 0
    if ! helper_ok; then HELPER_REPORT="unreachable"; return 0; fi
    local json
    if ! json="$(helper_ps "$HELPER_SERVICES_PS1" status -Json 2>/dev/null)"; then
        HELPER_REPORT="not-installed"
        return 0
    fi
    HELPER_REPORT="$(python3 -c '
import json, sys
try:
    data = json.loads(sys.argv[1])
except ValueError:
    print("not-installed"); sys.exit()
for s in data if isinstance(data, list) else [data]:
    print(str(s.get("name") or "") + "\t" + str(s.get("state") or "unknown"))
' "$json")"
}

helper_state() {    # helper_state <service>
    helper_report
    case "$HELPER_REPORT" in
        unreachable) echo "helper unreachable"; return ;;
        not-installed) echo "not installed (./localllm.sh install-helper)"; return ;;
    esac
    local line
    line="$(printf '%s\n' "$HELPER_REPORT" | awk -F'\t' -v n="$1" '$1 == n { print $2 }')"
    echo "${line:-not installed (./localllm.sh install-helper)}"
}

do_status() {   # do_status all|main|helper|<service>
    local target="$1" s place url state rows=()
    local services=()
    case "$target" in
        all) mapfile -t services < <(all_services) ;;
        main | helper)
            [[ "$target" == helper ]] && require_split
            for s in $(all_services); do [[ "$(where "$s")" == "$target" ]] && services+=("$s"); done
            ;;
        *) [[ -n "$(where "$target")" ]] || die "$target doesn't run in $LOCALLLM_MODE mode."; services=("$target") ;;
    esac
    echo "Mode: $LOCALLLM_MODE, apps: $LOCALLLM_APPS${HELPER_HOST:+ (helper PC: $HELPER_HOST)}"
    for s in "${services[@]}"; do
        place="$(where "$s")"
        [[ -n "$place" ]] || continue
        url="$(service_field "$s" url)"
        # Only Open WebUI and the search gateway are reachable over Tailscale; the helper's others listen on its 127.0.0.1.
        if [[ "$place" == helper && "$s" != open-webui && "$s" != search-gateway ]]; then
            url="on the helper: 127.0.0.1:$(service_field "$s" port)"
        fi
        if [[ "$place" == main ]]; then state="$(main_state "$s")"; else state="$(helper_state "$s")"; fi
        [[ "$state" == running ]] || FAILED+=("$s")
        rows+=("$s"$'\t'"$( [[ $place == main ]] && echo "main PC" || echo "helper PC")"$'\t'"${url:--}"$'\t'"$state")
    done
    printf '%s\n' "SERVICE"$'\t'"RUNS ON"$'\t'"ADDRESS"$'\t'"STATE" "${rows[@]}" | column -t -s $'\t'
    if [[ -n "$(where search-gateway)" ]] && [[ "$target" == all || "$target" == search-gateway ]]; then
        echo "Search usage dashboard: $(service_field search-gateway url)/dashboard"
    fi
    [[ "$target" == all ]] && echo "Last backup: $(last_backup_age)"
    # status reports; it doesn't fail because something is stopped.
    FAILED=()
}

# The newest encrypted archive in BACKUP_ARCHIVE_DIR and how old it is (status shows it: a rebuild is only as recent).
last_backup_age() {
    local newest age
    [[ -n "${BACKUP_ARCHIVE_DIR:-}" && -d "${BACKUP_ARCHIVE_DIR:-}" ]] || { echo "unknown (BACKUP_ARCHIVE_DIR not set in local.env)"; return; }
    newest="$(find "$BACKUP_ARCHIVE_DIR" -maxdepth 1 -name 'LocalLLM-backup-*.tar.gz.gpg' -printf '%T@ %f\n' 2>/dev/null | sort -n | tail -1)"
    [[ -n "$newest" ]] || { echo "none in $BACKUP_ARCHIVE_DIR (./archive-backup.sh)"; return; }
    age=$(( ($(date +%s) - ${newest%%.*}) / 3600 ))
    if [[ $age -ge 48 ]]; then
        echo "${newest#* }, $((age / 24)) days ago (the scheduled backup: journalctl --user -u localllm-backup)"
    else
        echo "${newest#* }, $age h ago"
    fi
}

# --- setup-helper ---

SSH_CONFIG="$HOME/.ssh/config"
SSH_BEGIN="# >>> $HELPER_SSH (written by ./localllm.sh setup-helper; edit HELPER_HOST/HELPER_USER in local.env instead)"
SSH_END="# <<< $HELPER_SSH"

# set_local_env <NAME> <value>: sets NAME="value" in local.env (replacing its line, else appending), mode 600.
set_local_env() {
    local name="$1" value="$2" tmp
    mkdir -p "$(dirname -- "$LOCAL_ENV_FILE")"
    touch "$LOCAL_ENV_FILE"
    chmod 600 "$LOCAL_ENV_FILE"
    tmp="$(mktemp "$LOCAL_ENV_FILE.XXXXXX")"
    NAME="$name" VALUE="$value" python3 - "$LOCAL_ENV_FILE" "$tmp" <<'PY'
import os, re, shlex, sys
src, dst = sys.argv[1], sys.argv[2]
name, value = os.environ["NAME"], os.environ["VALUE"]
line = f"{name}={shlex.quote(value) if value else chr(34) * 2}\n"
lines = open(src).readlines()
done = False
for i, l in enumerate(lines):
    if re.match(rf"\s*{name}=", l):
        lines[i] = line if not done else ""
        done = True
if not done:
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lines.append(line)
open(dst, "w").write("".join(lines))
PY
    chmod 600 "$tmp"
    mv "$tmp" "$LOCAL_ENV_FILE"
}

# ssh_config_block: the Host block for the helper PC, from HELPER_HOST and HELPER_USER.
ssh_config_block() {
    printf '%s\n' "$SSH_BEGIN" "Host $HELPER_SSH" "    HostName $HELPER_HOST" "    User $HELPER_USER" \
        "    IdentityFile ~/.ssh/$(basename -- "$HELPER_SSH_KEY")" "    IdentitiesOnly yes" \
        "    ServerAliveInterval 30" \
        "    # Windows' OpenSSH has no post-quantum key exchange yet; the connection runs inside Tailscale's encrypted" \
        "    # tunnel anyway, so ssh's warning about it is turned off for this host only." \
        "    WarnWeakCrypto no-pq-kex" "$SSH_END"
}

# write_ssh_config: puts the block at the top of ~/.ssh/config (the first value ssh finds wins, so a later "Host *"
# can't change it), replacing the old block; the rest of the file stays as it is.
write_ssh_config() {
    local block rest=""
    block="$(ssh_config_block)"
    mkdir -p "$(dirname -- "$SSH_CONFIG")"
    chmod 700 "$(dirname -- "$SSH_CONFIG")"
    if [[ -f "$SSH_CONFIG" ]]; then
        rest="$(awk -v b="$SSH_BEGIN" -v e="$SSH_END" '$0 == b { skip = 1; next } skip && $0 == e { skip = 0; next } !skip' "$SSH_CONFIG")"
    fi
    if [[ -f "$SSH_CONFIG" ]] && [[ "$(awk -v b="$SSH_BEGIN" -v e="$SSH_END" '$0 == b { on = 1 } on { print } $0 == e { exit }' "$SSH_CONFIG")" == "$block" ]]; then
        echo "$SSH_CONFIG: the $HELPER_SSH block is up to date."
        return 0
    fi
    { printf '%s\n' "$block"; [[ -n "$rest" ]] && printf '\n%s\n' "${rest#$'\n'}"; } > "$SSH_CONFIG.tmp"
    chmod 600 "$SSH_CONFIG.tmp"
    mv "$SSH_CONFIG.tmp" "$SSH_CONFIG"
    echo "$SSH_CONFIG: wrote the $HELPER_SSH block ($HELPER_USER@$HELPER_HOST)."
}

# The helper PC's Tailscale name: the Windows machines in this tailnet, as "DNS name<TAB>host name" lines.
tailnet_windows_peers() {
    tailscale status --json 2>/dev/null | python3 -c '
import json, sys
try:
    peers = (json.load(sys.stdin).get("Peer") or {}).values()
except ValueError:
    peers = []
for p in peers:
    if str(p.get("OS", "")).lower() == "windows":
        dns, host = str(p.get("DNSName") or "").rstrip("."), str(p.get("HostName") or "")
        print(dns + "\t" + host)
'
}

ask() {     # ask <question> <default>: prints the answer (the default on Enter)
    local answer
    read -r -p "$1${2:+ [$2]}: " answer
    printf '%s' "${answer:-$2}"
}

do_setup_helper() {
    log "Tailscale on this PC"
    command -v tailscale >/dev/null || die "Tailscale is not installed: run install/tailscale.sh (or ./install.sh)."
    local backend
    backend="$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("BackendState",""))' 2>/dev/null)"
    [[ "$backend" == Running ]] || die "Tailscale isn't connected on this PC ($backend): run install/tailscale.sh and sign in."
    echo "Connected."

    log "The helper PC's name and Windows user (saved in $LOCAL_ENV_FILE)"
    local host="${HELPER_HOST:-}" user="${HELPER_USER:-}" peers suggestion=""
    if [[ -z "$host" ]]; then
        peers="$(tailnet_windows_peers)"
        if [[ -n "$peers" ]]; then
            echo "Windows PCs in your tailnet:"
            printf '%s\n' "$peers" | awk -F'\t' '{ printf "  %s (%s)\n", $1, $2 }'
            [[ "$(printf '%s\n' "$peers" | wc -l)" -eq 1 ]] && suggestion="$(printf '%s' "$peers" | cut -f1)"
        else
            echo "No Windows PC in your tailnet yet: install Tailscale on the helper PC and sign in with the same account."
        fi
        host="$(ask "The helper PC's Tailscale name" "$suggestion")"
        [[ -n "$host" ]] || die "No name given: nothing changed."
    fi
    if [[ -z "$user" ]]; then
        echo "The helper's Windows user name: run whoami in a PowerShell window there; it prints <pc>\\<user>."
        user="$(ask "Its Windows user name (the part after the \\)" "")"
        user="${user##*\\}"
        [[ -n "$user" ]] || die "No user name given: nothing changed."
    fi
    [[ "$host" =~ ^[A-Za-z0-9.-]+$ ]] || die "'$host' doesn't look like a Tailscale name (letters, digits, - and .)."
    [[ "$user" =~ ^[^[:space:]\"\'\\/]+$ ]] || die "'$user' doesn't look like a Windows user name (must not contain spaces, quotes or slashes; use an administrator account without spaces)."
    if [[ "$host" != "${HELPER_HOST:-}" ]]; then set_local_env HELPER_HOST "$host"; echo "HELPER_HOST=$host"; fi
    if [[ "$user" != "${HELPER_USER:-}" ]]; then set_local_env HELPER_USER "$user"; echo "HELPER_USER=$user"; fi
    HELPER_HOST="$host" HELPER_USER="$user"
    echo "To change them later: edit HELPER_HOST and HELPER_USER in $LOCAL_ENV_FILE, then run this again."

    log "SSH key and ~/.ssh/config"
    if [[ -f "$HELPER_SSH_KEY" ]]; then
        echo "Key: $HELPER_SSH_KEY (exists)"
    else
        mkdir -p "$(dirname -- "$HELPER_SSH_KEY")" && chmod 700 "$(dirname -- "$HELPER_SSH_KEY")"
        ssh-keygen -q -t ed25519 -N "" -C "$HELPER_SSH" -f "$HELPER_SSH_KEY" || die "ssh-keygen failed."
        echo "Key: $HELPER_SSH_KEY (new)"
    fi
    write_ssh_config

    log "Connection test"
    # The first connection asks you to confirm the helper's fingerprint: compare it with the one bootstrap.ps1 printed
    # on the helper's screen. Afterwards ssh remembers it (~/.ssh/known_hosts).
    local version errors bootstrap_out="$HOME/.cache/localllm/bootstrap.local.ps1"
    errors="$(mktemp)"
    # Only the version is kept from the output; ssh's own messages (a new fingerprint added) go to the screen.
    if version="$(ssh -F "$SSH_CONFIG" -o ConnectTimeout=8 -o PasswordAuthentication=no "$HELPER_SSH" '$PSVersionTable.PSVersion.ToString()' 2>"$errors")"; then
        grep -v '^\*\*' "$errors" >&2
        rm -f "$errors" "$bootstrap_out"
        echo "OK: $HELPER_SSH answers (PowerShell ${version//$'\r'/})."
        return 0
    fi
    # A reinstalled helper (or bootstrap.ps1 run on a new Windows) has a new SSH host key, while known_hosts (restored
    # from the backup) holds the old one: ssh refuses. Remove the old one only when you know the helper was reinstalled.
    if grep -q 'REMOTE HOST IDENTIFICATION HAS CHANGED' "$errors"; then
        rm -f "$errors"
        echo "The helper's SSH fingerprint is not the one this PC knows. That is expected after reinstalling Windows on"
        echo "the helper (or running bootstrap.ps1 on a new one); otherwise something else answers at $HELPER_HOST: stop."
        if [[ -t 0 ]] && [[ "$(ask "The helper was reinstalled: forget its old fingerprint? (yes/no)" "no")" == yes ]]; then
            ssh-keygen -R "$HELPER_HOST" -f "$HOME/.ssh/known_hosts" >/dev/null 2>&1
            echo "Old fingerprint removed. Running the connection test again: compare the new fingerprint with the one"
            echo "bootstrap.ps1 printed on the helper's screen."
            do_setup_helper
            return
        fi
        die "Nothing changed. After a reinstall of the helper, run ./localllm.sh setup-helper again and answer yes."
    fi
    echo "Not reachable yet: $(tail -1 "$errors")"
    rm -f "$errors"
    mkdir -p "$(dirname -- "$bootstrap_out")"
    sed "s|@MAIN_PC_PUBLIC_KEY@|$(cat "$HELPER_SSH_KEY.pub")|" "$REPO_DIR/windows/bootstrap.ps1" > "$bootstrap_out"
    echo
    echo "Wrote $bootstrap_out (this PC's public key filled in; deleted once setup-helper connects). On the helper PC:"
    echo "  1. Copy that file there (USB stick, or any way you like: it holds only a public key)."
    echo "  2. Open PowerShell as Administrator (Start, type powershell, \"Run as administrator\") and run:"
    printf '%s\n' '       powershell -ExecutionPolicy Bypass -File <path to>\bootstrap.local.ps1'
    echo "  3. Note the fingerprint it prints at the end."
    echo "Then run ./localllm.sh setup-helper here again: answer \"yes\" when ssh shows the same fingerprint."
    return 1
}

# --- apps: pi, pi,owui ---

# Open WebUI and every service only Open WebUI uses, in helper stop order (search-gateway stays on for Pi).
OWUI_HELPER_STOP=(open-webui comfyui small-model docling whisper voice-router kokoro)

normalize_apps() {
    local raw="${1,,}" part has_pi=0 has_owui=0
    local IFS=','
    for part in $raw; do
        part="${part// /}"
        [[ -z "$part" ]] && continue
        case "$part" in
            pi) has_pi=1 ;;
            owui) has_owui=1 ;;
            *) die "Usage: ./localllm.sh apps [pi|pi,owui]" ;;
        esac
    done
    if [[ $has_pi -eq 1 && $has_owui -eq 0 ]]; then echo "pi"
    elif [[ $has_pi -eq 1 && $has_owui -eq 1 ]]; then echo "pi,owui"
    else die "Usage: ./localllm.sh apps [pi|pi,owui]"
    fi
}

do_apps() {
    if [[ -z "${1:-}" ]]; then
        echo "LOCALLLM_APPS=$LOCALLLM_APPS (change with: ./localllm.sh apps pi | pi,owui)"
        return 0
    fi
    local prev="$LOCALLLM_APPS" want changed=0 llama_was_running=false s
    want="$(normalize_apps "$1")"
    [[ "$prev" != "$want" ]] && changed=1
    local had_owui=0 want_owui=0
    [[ ",$prev," == *,owui,* ]] && had_owui=1
    [[ ",$want," == *,owui,* ]] && want_owui=1

    if [[ $changed -eq 1 ]]; then
        [[ "$(main_state llama)" == running ]] && llama_was_running=true

        local stop_main=() stop_helper=()
        if [[ $had_owui -eq 1 && $want_owui -eq 0 ]]; then
            for s in open-webui kokoro; do [[ "$(where "$s")" == main ]] && stop_main+=("$s"); done
            for s in "${OWUI_HELPER_STOP[@]}"; do [[ "$(where "$s")" == helper ]] && stop_helper+=("$s"); done
        fi

        if [[ ${#stop_main[@]} -gt 0 || ${#stop_helper[@]} -gt 0 ]]; then
            log "Stopping disabled services"
            for s in "${stop_main[@]}"; do run_main stop "$s"; done
            if [[ "$LOCALLLM_MODE" == split && ${#stop_helper[@]} -gt 0 ]]; then
                if helper_ok; then
                    local ps_list
                    ps_list="$(printf "'%s'," "${stop_helper[@]}")"
                    helper_ssh "foreach (\$s in @(${ps_list%,})) { powershell -NoProfile -ExecutionPolicy Bypass -File '$HELPER_SERVICES_PS1' stop \$s }" \
                        || warn "Some helper services didn't stop cleanly."
                else
                    helper_unreachable_message
                    warn "Helper PC couldn't be reached to stop disabled services; its config will update on the next deploy."
                fi
            fi
        fi

        log "Setting LOCALLLM_APPS=$want"
        set_local_env LOCALLLM_APPS "$want"
        LOCALLLM_APPS="$want"
    fi

    refresh_endpoints force || die "$ENDPOINTS_ERROR"
    # shellcheck source=/dev/null
    source "$ENDPOINTS_ENV"
    log "Syncing Pi config"
    "$REPO_DIR/install/pi-models.sh"
    "$REPO_DIR/install/pi-config.sh"

    if [[ "$LOCALLLM_MODE" == split && ( $changed -eq 1 || $want_owui -eq 1 ) ]]; then
        if helper_ok; then
            deploy_files --no-checks
        elif [[ $want_owui -eq 1 ]]; then
            helper_unreachable_message
            FAILED+=("helper PC")
        fi
    fi

    if [[ $changed -eq 1 ]] && $llama_was_running; then
        log "Restarting llama-server for $want"
        sudo systemctl restart localllm-llm
        wait_for "$LLAMA_URL/health" 300 || { warn "llama-server didn't come back: journalctl -u localllm-llm -e"; FAILED+=("llama"); }
        if [[ $had_owui -eq 0 && $want_owui -eq 1 ]]; then
            log "Starting enabled services for $want"
            for s in open-webui kokoro; do [[ "$(where "$s")" == main ]] && run_main start "$s"; done
            if [[ "$LOCALLLM_MODE" == split ]]; then
                expose_main_ports
                run_helper start all
            fi
        fi
    fi

    if [[ $want_owui -eq 1 ]]; then
        if [[ "$(where open-webui)" == main ]]; then
            if webui_running; then
                log "Syncing Open WebUI config"
                "$REPO_DIR/install/open-webui-config.sh" || { warn "install/open-webui-config.sh failed."; FAILED+=("open-webui-config"); }
                "$REPO_DIR/install/chat-types.sh" || { warn "install/chat-types.sh failed."; FAILED+=("chat-types"); }
            else
                echo "Open WebUI isn't running: run install/open-webui-config.sh and install/chat-types.sh once it's up."
            fi
        elif [[ "$(where open-webui)" == helper ]] && helper_ok; then
            if helper_ps "$HELPER_WIN\\services.ps1" status open-webui -Json 2>/dev/null | grep -q '"running"'; then
                log "Syncing Open WebUI config on the helper PC"
                helper_task sync || { warn "Open WebUI sync on the helper PC failed."; FAILED+=("open-webui-config"); }
                if [[ -d "$CHAT_TYPES_DIR" ]] && compgen -G "$CHAT_TYPES_DIR/*.json" >/dev/null; then
                    helper_task chat-types || { warn "Chat types sync on the helper PC failed."; FAILED+=("chat-types"); }
                fi
            else
                echo "Open WebUI isn't running on the helper: its tools and chat types are pushed once it's up."
            fi
        fi
    fi

    echo
    if [[ $changed -eq 0 ]]; then
        echo "Already set to $want (linked installs synced)."
    else
        case "$want" in
            pi)
                echo "Pi-only mode (LOCALLLM_APPS=pi): Open WebUI and its exclusive services are off, and Pi gets 1 full slot (2x context)."
                echo "Start a new Pi session (or /reload) to use the new context window."
                ;;
            pi,owui)
                echo "Pi + Open WebUI mode (LOCALLLM_APPS=pi,owui): Open WebUI services enabled, 2 slots."
                echo "Start a new Pi session (or /reload) to use the restored context window."
                ;;
        esac
    fi
}

# --- pages that only answer on the PC they run on (README, "Pages in split mode") ---

PAGE_PORT=8090   # the search gateway's: the pages' addresses are the same in both modes

page_port_free() {
    if curl -s -m 2 -o /dev/null "http://127.0.0.1:$PAGE_PORT/" 2>/dev/null; then
        die "Something already answers on 127.0.0.1:$PAGE_PORT (another ./localllm.sh settings or chat-types? Ctrl+C it first)."
    fi
}

# settings: the search gateway's settings page answers only on its own PC. In split mode an SSH tunnel makes the
# helper's 127.0.0.1:8090 this PC's, for as long as it runs; the gateway then sees a client on its own PC.
do_settings() {
    if [[ "$(where search-gateway)" == main ]]; then
        echo "Search settings: http://127.0.0.1:$PAGE_PORT/settings (the search gateway runs on this PC)."
        return 0
    fi
    require_helper_connection
    page_port_free
    echo "Search settings (the helper PC's search gateway, through SSH):"
    echo "  http://127.0.0.1:$PAGE_PORT/settings"
    echo "Open it in the browser; Ctrl+C here closes the connection when you're done."
    ssh -F "$HOME/.ssh/config" -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
        -L "127.0.0.1:$PAGE_PORT:127.0.0.1:$PAGE_PORT" "$HELPER_SSH"
    local code=$?
    [[ $code -eq 0 || $code -eq 130 || $code -eq 255 ]] || return $code
}

# chat-types: the chat types page edits this PC's files (~/.config/localllm/chat-types). In single mode the search
# gateway here serves it; in split mode services/chat_types_server.py does, building the presets on the helper.
do_chat_types() {
    apps_has owui \
        || die "Open WebUI is off (LOCALLLM_APPS=$LOCALLLM_APPS): ./localllm.sh apps pi,owui turns it back on."
    if [[ "$(where open-webui)" == main ]]; then
        echo "Chat types: http://127.0.0.1:$PAGE_PORT/chat-types (served by the search gateway on this PC)."
        return 0
    fi
    require_helper_connection
    page_port_free
    echo "The chat types page, on this PC (Open WebUI and the presets are on the helper PC):"
    python3 "$REPO_DIR/services/chat_types_server.py" "$PAGE_PORT"
}

# --- main ---

main() {
    local cmd="${1:-}" target="${2:-all}"
    [[ $# -le 2 ]] || usage
    case "$cmd" in
        start | stop | restart | status)
            [[ -z "$ENDPOINTS_ERROR" ]] || die "$ENDPOINTS_ERROR"
            if [[ "$target" != all && "$target" != main && "$target" != helper ]] && ! all_services | grep -qx -- "$target"; then
                die "No service '$target'. Services: $(all_services | tr '\n' ' ')(or all, main, helper)"
            fi
            case "$cmd" in
                start) do_start "$target" ;;
                stop) do_stop "$target" ;;
                restart) do_stop "$target"; do_start "$target" ;;
                status) do_status "$target" ;;
            esac
            if [[ "$cmd" == start && ( "$target" == all || "$target" == main ) ]]; then
                echo
                echo "Pi: run 'pi' in a project folder"
            fi
            ;;
        endpoints)
            [[ $# -eq 1 ]] || usage
            refresh_endpoints force || die "$ENDPOINTS_ERROR"
            # shellcheck source=/dev/null
            source "$ENDPOINTS_ENV"
            echo "Wrote $ENDPOINTS_FILE"
            do_status all
            ;;
        setup-helper)
            [[ $# -eq 1 ]] || usage
            do_setup_helper
            exit $?
            ;;
        install-helper)
            do_install_helper "${2:-}"
            ;;
        deploy)
            [[ $# -eq 1 ]] || usage
            require_helper_connection
            load_helper_env
            deploy_files
            apply_deployed
            ;;
        switch)
            [[ $# -eq 2 ]] || usage
            do_switch "$2"
            ;;
        apps)
            do_apps "${2:-}"
            ;;
        restore-helper-data)
            [[ $# -eq 1 ]] || usage
            do_restore_helper_data
            ;;
        gaming)
            [[ $# -eq 2 && "$2" =~ ^(on|off|status)$ ]] || usage
            require_split
            require_helper_connection
            helper_ps "$HELPER_WIN\\gaming.ps1" "$2" || FAILED+=("gaming $2")
            ;;
        settings)
            [[ $# -eq 1 ]] || usage
            do_settings || FAILED+=("settings")
            ;;
        chat-types)
            [[ $# -eq 1 ]] || usage
            do_chat_types || FAILED+=("chat-types")
            ;;
        monitor)
            [[ $# -le 2 ]] || usage
            if [[ $# -eq 2 && "$2" == --detect-hw ]]; then
                python3 "$REPO_DIR/tools/monitor/monitor.py" --detect-hw || FAILED+=("monitor")
            elif [[ $# -eq 2 ]]; then
                [[ "$2" =~ ^[0-9]+$ ]] || die "Usage: ./localllm.sh monitor [port|--detect-hw]"
                python3 "$REPO_DIR/tools/monitor/monitor.py" --port "$2" || FAILED+=("monitor")
            else
                python3 "$REPO_DIR/tools/monitor/monitor.py" || FAILED+=("monitor")
            fi
            ;;
        -h | --help | help | "") usage ;;
        *) usage ;;
    esac

    if [[ ${#FAILED[@]} -gt 0 ]]; then
        echo
        warn "Not done: ${FAILED[*]}"
        exit 1
    fi
}

# Sourced (by the guard tests), it only defines the functions.
[[ "${BASH_SOURCE[0]}" != "$0" ]] || main "$@"
