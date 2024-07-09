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

IGNORE_FILE="$RESTORABILITY_IGNORE_FILE"
LOCAL_IGNORED=()
[[ -f "$IGNORE_FILE" ]] && mapfile -t LOCAL_IGNORED < <(grep -v -E '^\s*(#|$)' "$IGNORE_FILE")

FAIL=0
ok()   { printf '  ok    %s\n' "$*"; }
bad()  { printf '  FAIL  %s\n' "$*"; FAIL=1; }
note()   { printf '  note  %s\n' "$*"; }

# check_list <label> <listing command output> <covered names...> -- <ignored names...>
# Every name in the listing must be covered (brought back) or ignored (caches, other projects).
check_list() {
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
check_list "~/.config/localllm" "$(ls -A "$HOME/.config/localllm" 2>/dev/null)" \
    "local.env" "restorability-ignore" "chat-types" \
    -- "model.json" "hardware.json"

check_list "~/.local/bin" "$(ls "$HOME/.local/bin" 2>/dev/null)" \
    "localllm.sh" "llama.sh" "model.sh" "server.sh" \
    -- \
    "${LOCAL_IGNORED[@]}"

# Exit with failure if anything was not covered
if [ $FAIL -ne 0 ]; then
    echo ""
    echo "RESTORABILITY CHECK FAILED: some items are not covered by backup or scripts."
    exit 1
fi

echo ""
echo "RESTORABILITY CHECK PASSED"
exit 0
