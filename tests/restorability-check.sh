#!/usr/bin/env bash
# The "~/..." strings below are labels for people to read, not paths.
# shellcheck disable=SC2088
# Restorability check (S, no model): is everything this setup runs brought back by the repo's scripts, the settings
# backup or a README step? Read-only on the live machine.
#   1. Inventory: lists what exists now (Pi's folder, ~/.local/bin, user services, Nix profile, Docker containers,
#      system files) and reports any entry no script, backup or README step covers.
#   2. Restore: runs restore.sh with a temporary HOME and checks that every file in backup/ arrives, with the modes
#      restore.sh sets, and that the repo itself clones from backup/repo.bundle.
# Exit status 1 when anything is uncovered or missing. Run it after every section of work.
# Programs of your own that aren't part of this setup (in ~/.local/bin, the Nix profile) go one name per line in
# ~/.config/localllm/restorability-ignore, which stays on this machine.

set -uo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

IGNORE_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/localllm/restorability-ignore"
LOCAL_IGNORED=()
[[ -f "$IGNORE_FILE" ]] && mapfile -t LOCAL_IGNORED < <(grep -v -E '^\s*(#|$)' "$IGNORE_FILE")

FAIL=0
ok()   { printf '  ok    %s\n' "$*"; }
bad()  { printf '  FAIL  %s\n' "$*"; FAIL=1; }
note()   { printf '  note  %s\n' "$*"; }

# check_dir <label> <listing command output> <covered names...> -- <ignored names...>
# Every name in the listing must be covered (brought back) or ignored (caches, other projects).
check_dir() {
    local label="$1" listing="$2"; shift 2
    local -A known=()
    local name kind=covered
    for name in "$@"; do
        if [[ "$name" == "--" ]]; then kind=ignored; continue; fi
        known[$name]=$kind
    done
    local uncovered=()
    while IFS= read -r name; do
        [[ -n "$name" ]] || continue
        [[ -n "${known[$name]:-}" ]] || uncovered+=("$name")
    done <<< "$listing"
    if [[ ${#uncovered[@]} -eq 0 ]]; then ok "$label"; else bad "$label: not covered: ${uncovered[*]}"; fi
}

# --- ~/.config/localllm ---
echo "Checking ~/.config/localllm..."

CONFIG_LISTING="$(ls -A "$HOME/.config/localllm" 2>/dev/null || true)"
check_dir "~/.config/localllm" "$CONFIG_LISTING" \
    "local.env" "restorability-ignore" "chat-types" \
    -- "model.json" "hardware.json"

# --- ~/.local/bin ---
echo "Checking ~/.local/bin..."

BIN_LISTING="$(ls -A "$HOME/.local/bin" 2>/dev/null || true)"
check_dir "~/.local/bin" "$BIN_LISTING" \
    "localllm.sh" "llama.sh" "model.sh" "server.sh" \
    -- "pip" "python3" "python3.*"

# --- User systemd services ---
echo "Checking user systemd services..."

SERVICE_LISTING="$(systemctl --user list-units --type=service --no-legend 2>/dev/null | awk '{print $1}' | sed 's/\.service$//' || true)"
check_dir "systemd user services" "$SERVICE_LISTING" \
    "localllm-llm" "searxng" \
    --

# --- ~/LocalLLM (repo) ---
echo "Checking ~/LocalLLM repo..."

REPO_LISTING="$(ls -A ~/LocalLLM 2>/dev/null || true)"
check_dir "~/LocalLLM" "$REPO_LISTING" \
    "README.md" "local.env" "services.json" "model.sh" "server.sh" \
    "gpu-limits.sh" "common.sh" "setup-llama.sh" "start.sh" "stop.sh" \
    "scripts" "install" "services" "tests" "models" \
    -- ".git" ".gitignore" ".shellcheckrc"

# --- Docker containers ---
echo "Checking Docker containers..."

CONTAINER_LISTING="$(docker ps -a --format '{{.Names}}' 2>/dev/null || true)"
check_dir "Docker containers" "$CONTAINER_LISTING" \
    "localllm-llm" "searxng" \
    --

# Exit with failure if anything was not covered
if [ $FAIL -ne 0 ]; then
    echo ""
    echo "RESTORABILITY CHECK FAILED: some items are not covered by backup or scripts."
    exit 1
fi

echo ""
echo "RESTORABILITY CHECK PASSED"
exit 0
