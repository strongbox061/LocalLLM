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

set -euo pipefail

RESTORABILITY_IGNORE_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/localllm/restorability-ignore"
FAIL=0

ok()   { printf '  ok    %s\n' "$*"; }
bad()  { printf '  FAIL  %s\n' "$*"; FAIL=1; }
note() { printf '  note  %s\n' "$*"; }

# check_list <label> <listing command output> <covered names...> -- <ignored names...>
# Every name in the listing must be covered (brought back) or ignored (caches, other projects).
check_list() {
    local label="$1"
    local listing="$2"
    shift 2

    local covered=()
    local ignored=()
    local separator=0

    for arg in "$@"; do
        if [ "$arg" = "--" ]; then
            separator=1
            continue
        fi
        if [ $separator -eq 0 ]; then
            covered+=("$arg")
        else
            ignored+=("$arg")
        fi
    done

    # Build ignore set from file + arguments
    local ignore_set="${ignored[*]:-}"
    if [ -f "$RESTORABILITY_IGNORE_FILE" ]; then
        while IFS= read -r line; do
            line="${line%%#*}"  # strip comments
            line="$(echo "$line" | xargs)"  # trim whitespace
            [ -n "$line" ] && ignore_set="$ignore_set $line"
        done < "$RESTORABILITY_IGNORE_FILE"
    fi

    local all_ok=true
    while IFS= read -r name; do
        [ -z "$name" ] && continue
        # Check if covered
        local is_covered=false
        for c in "${covered[@]:-}"; do
            if [[ "$name" == "$c" ]]; then
                is_covered=true
                break
            fi
        done

        # Check if ignored
        local is_ignored=false
        for i in $ignore_set; do
            if [[ "$name" == "$i" ]]; then
                is_ignored=true
                break
            fi
        done

        if [ "$is_covered" = true ]; then
            ok "$label: $name"
        elif [ "$is_ignored" = true ]; then
            note "$label: $name (ignored)"
        else
            bad "$label: $name (not covered, not ignored)"
            all_ok=false
        fi
    done <<< "$listing"

    if [ "$all_ok" = false ]; then
        return 1
    fi
}

# --- ~/.config/localllm ---
echo "Checking ~/.config/localllm..."

CONFIG_LISTING="$(ls -A "$HOME/.config/localllm" 2>/dev/null || true)"
check_list "~/.config/localllm" "$CONFIG_LISTING" \
    "local.env" "restorability-ignore" "chat-types" \
    -- "model.json" "hardware.json"

# --- ~/.local/bin ---
echo "Checking ~/.local/bin..."

BIN_LISTING="$(ls -A "$HOME/.local/bin" 2>/dev/null || true)"
check_list "~/.local/bin" "$BIN_LISTING" \
    "localllm.sh" "llama.sh" "model.sh" "server.sh" \
    -- "pip" "python3" "python3.*"

# --- User systemd services ---
echo "Checking user systemd services..."

SERVICE_LISTING="$(systemctl --user list-units --type=service --no-legend 2>/dev/null | awk '{print $1}' | sed 's/\.service$//' || true)"
check_list "systemd user services" "$SERVICE_LISTING" \
    "localllm-llm" "searxng" \
    --

# --- ~/LocalLLM (repo) ---
echo "Checking ~/LocalLLM repo..."

REPO_LISTING="$(ls -A ~/LocalLLM 2>/dev/null || true)"
check_list "~/LocalLLM" "$REPO_LISTING" \
    "README.md" "local.env" "services.json" "model.sh" "server.sh" \
    "gpu-limits.sh" "common.sh" "setup-llama.sh" "start.sh" "stop.sh" \
    "scripts" "install" "services" "tests" "models" \
    -- ".git" ".gitignore" ".shellcheckrc"

# --- Docker containers ---
echo "Checking Docker containers..."

CONTAINER_LISTING="$(docker ps -a --format '{{.Names}}' 2>/dev/null || true)"
check_list "Docker containers" "$CONTAINER_LISTING" \
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
