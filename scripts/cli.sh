#!/usr/bin/env bash
# One command for the whole setup, on one PC (single mode) or on the main PC and the helper PC (split mode; README,
# "Two-PC setup"). The mode and the helper's name come from ~/.config/localllm/local.env (LOCALLLM_MODE, HELPER_HOST,
# HELPER_USER); where each service runs and its address from config/services.json (lib/endpoints.py).
#
#   ./scripts/cli.sh start|stop|restart|status            every service on both PCs (start.sh and stop.sh run these)
#   ./scripts/cli.sh start|stop|restart|status main       only the main PC's
#   ./scripts/cli.sh start|stop|restart|status helper     only the helper PC's (windows\services.ps1 there, over SSH)
#   ./scripts/cli.sh start|stop|restart|status <service>  one service, wherever it runs (names: ./scripts/cli.sh status)
#   ./scripts/cli.sh endpoints                            writes endpoints.json again and shows where everything runs
#   ./scripts/cli.sh setup-helper                         the SSH connection to the helper PC (README, "Connecting the
#                                                      helper PC"); safe to run again

#   ./scripts/cli.sh install-helper [step,step]           installs (or updates) everything on the helper PC; safe to run
#                                                      again (steps: windows/setup.ps1)
#   ./scripts/cli.sh deploy                               sends the repo's changed files and the helper's settings to
#                                                      it, after the guard checks, and applies them
#                                                      other PC, with checks, and runs everything there
#   ./scripts/cli.sh apps [pi|pi,owui]                    shows or switches which apps run (pi = Pi only: 1 full slot
#                                                      back on it, with checks (README, "Rebuilding only the helper
#                                                      PC")
#   ./scripts/cli.sh gaming on|off|status                 the helper's gaming mode (frees its GPU for a game)
#   ./scripts/cli.sh settings                             the search settings page: in split mode it opens an SSH
#                                                      tunnel to the helper's and waits (Ctrl+C closes it)
#   ./scripts/cli.sh chat-types                           the chat types page: in split mode it serves it here, from
#                                                      this PC's files, and waits (Ctrl+C ends it)
#   ./scripts/cli.sh monitor [port]                       web monitor for CPU, GPU and RAM on main and helper PCs, plus
#                                                      single/split and apps mode (default port 8095; Ctrl+C ends it)
# Order: start = the main PC's services first (llama-server first), then the helper's; stop = the helper's first,
# then the main PC's, llama-server last. SearXNG keeps running on stop (it starts at boot); `stop searxng` stops it.
# When the helper can't be reached, the main PC's part still runs and the helper is reported as unreachable.

set -uo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/lib/common.sh"
# shellcheck source=lib/helper.sh
source "$REPO_DIR/lib/helper.sh"

# The main PC's services, in start order; stop goes the other way round and leaves SearXNG running. Each has
# a status check that tests its HTTP endpoint.
MAIN_SERVICES=(llama-server searxng api)

# Helper services reference the PowerShell script on the helper.
HELPER_SERVICES_PS1="$HELPER_REPO\\windows\\services.ps1"

usage() {
    cat <<EOF
Usage: ./scripts/cli.sh <command> [target] [service]

Commands:
  start|stop|restart|status   Run the command on all services, main only, helper only, or a single service
  endpoints                   Regenerate endpoints.json and display addresses
  setup-helper                Set up SSH connection to the helper PC
  install-helper              Install or update everything on the helper PC
  deploy                      Send changed files and settings to the helper PC
  apps [mode]                 Show or switch running apps (pi, pi+owui)
  gaming on|off|status        Toggle gaming mode on the helper PC
  settings                    Open search settings page (SSH tunnel in split mode)
  chat-types                  Serve chat types page locally
  monitor [port]              Web monitor for CPU, GPU, RAM on both PCs

Targets: main, helper (or omit for all)
Services: llama-server, searxng, api, or any service from services.json
EOF
    exit 1
}

# The services config/services.json lists, in its order.
all_services() {
    python3 -c "
import json, sys
with open('$SERVICES_JSON') as f:
    cfg = json.load(f)
for svc in cfg.get('services', []):
    print(svc['name'])
"
}

# where <service> -> prints main, helper, or empty (doesn't run in this mode).
where() {
    local service="$1"
    python3 -c "
import json, sys
with open('$SERVICES_JSON') as f:
    cfg = json.load(f)
for svc in cfg.get('services', []):
    if svc['name'] == '$service':
        location = svc.get('location', '')
        print(location if location else 'none')
        break
"
}

main_script() {
    case "$1" in
        start)  "$SCRIPT_DIR/start.sh" "$@" ;;
        stop)   "$SCRIPT_DIR/stop.sh" "$@" ;;
        restart)
            "$SCRIPT_DIR/stop.sh" "$@" || true
            "$SCRIPT_DIR/start.sh" "$@"
            ;;
        status) "$SCRIPT_DIR/lib/check_status.sh" "$@" ;;
        *)      echo "Unknown command: $1" >&2; exit 1 ;;
    esac
}

FAILED=()
run_main() {
    local action="$1"
    local target="${2:-all}"
    local service="${3:-}"

    if [[ "$target" == "helper" ]]; then
        echo "[main] skipping helper target"
        return 0
    fi

    if [[ "$target" == "main" || -z "$target" ]]; then
        local services_to_run=()
        if [[ -n "$service" ]]; then
            services_to_run=("$service")
        else
            services_to_run=("${MAIN_SERVICES[@]}")
        fi

        for svc in "${services_to_run[@]}"; do
            echo "[main] $action $svc"
            if ! main_script "$action" "$svc"; then
                echo "[main] FAILED: $svc" >&2
                FAILED+=("$svc")
            fi
        done
    fi
}

HELPER_STATE=""   # "", ok or unreachable (checked once per run)
helper_ok() {
    if [[ -z "$HELPER_STATE" ]]; then
        if ssh -o ConnectTimeout=5 "$HELPER_USER@$HELPER_HOST" "echo ok" &>/dev/null; then
            HELPER_STATE="ok"
        else
            HELPER_STATE="unreachable"
        fi
    fi
    [[ "$HELPER_STATE" == "ok" ]]
}

run_helper() {
    local action="$1"
    local target="${2:-all}"
    local service="${3:-all}"

    if [[ "$target" == "main" ]]; then
        echo "[helper] skipping main target"
        return 0
    fi

    if ! helper_ok; then
        echo "[helper] unreachable (state=$HELPER_STATE)" >&2
        return 1
    fi

    local cmd=""
    case "$action" in
        start|stop|restart)
            cmd="$action"
            ;;
        status)
            cmd="status"
            ;;
        *)
            echo "[helper] unknown action: $action" >&2
            return 1
            ;;
    esac

    local args=""
    if [[ "$service" != "all" ]]; then
        args="$service"
    fi

    echo "[helper] $action $service (via SSH)"
    ssh "$HELPER_USER@$HELPER_HOST" \
        "cd '$HELPER_REPO' && bash windows/services.ps1 $cmd $args" 2>&1 || {
        echo "[helper] FAILED: $action $service" >&2
        HELPER_STATE="unreachable"
        return 1
    }
}

require_split() {
    if [[ "$LOCALLLM_MODE" != "split" ]]; then
        echo "This command requires split mode (LOCALLLM_MODE=split in $ENV_FILE)" >&2
        exit 1
    fi
}

# In split mode the helper PC reaches the main PC's llama-server and SearXNG over Tailscale: `tailscale serve`
# forwards their ports from the Tailscale address to 127.0.0.1 (both keep listening on 127.0.0.1 only; nothing is
# exposed beyond the Tailscale network).

# --- Command dispatch ---
CMD="${1:-}"
TARGET="${2:-all}"
SERVICE="${3:-}"

case "$CMD" in
    start|stop|restart|status)
        run_main "$CMD" "$TARGET" "$SERVICE"
        if [[ "$TARGET" != "main" ]]; then
            run_helper "$CMD" "$TARGET" "$SERVICE"
        fi
        if [[ ${#FAILED[@]} -gt 0 ]]; then
            echo "" >&2
            echo "Failed services: ${FAILED[*]}" >&2
            exit 1
        fi
        ;;
    endpoints)
        python3 "$SCRIPT_DIR/lib/endpoints.py"
        ;;
    setup-helper|install-helper|deploy|apps|gaming|settings|chat-types|monitor)
        echo "$CMD not yet implemented" >&2
        exit 1
        ;;
    *)
        usage
        ;;
esac
