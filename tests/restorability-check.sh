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

FAIL=0
ok()   { printf '  ok    %s\n' "$*"; }
bad()  { printf '  FAIL  %s\n' "$*"; FAIL=1; }
note() { printf '  note  %s\n' "$*"; }

# check_dir <label> <listing command output> <covered names...> -- <ignored names...>
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

IGNORE_FILE="$RESTORABILITY_IGNORE_FILE"
LOCAL_IGNORED=()
[[ -f "$IGNORE_FILE" ]] && mapfile -t LOCAL_IGNORED < <(grep -v -E '^\s*(#|$)' "$IGNORE_FILE")

echo "Private files"
# Open WebUI's signing key lets whoever reads it log in as any user; its database holds your chats; backup/ has copies
# of both. backup.sh, restore.sh and services/open-webui.sh keep them yours only (lib/common.sh make_private).
open=()
for f in "$BACKUP_DIR:700" "$WEBUI_DATA_DIR:700" "$WEBUI_KEY_FILE:600" "$WEBUI_DATA_DIR/webui.db:600"; do
    path="${f%:*}" want="${f##*:}"
    [[ -e "$path" ]] || continue
    mode="$(stat -c %a "$path")"
    [[ "$mode" == "$want" ]] || open+=("${path/#"$HOME"/\~} ($mode, should be $want)")
done
if [[ ${#open[@]} -eq 0 ]]; then ok "Open WebUI's signing key, its database and backup/ are yours only"
else bad "others can read: ${open[*]} (run ./backup.sh or restart Open WebUI)"; fi

echo
echo "Inventory"

check_list "~/.config/localllm" "$(ls -A "$HOME/.config/localllm" 2>/dev/null)" \
    "local.env" "restorability-ignore" "chat-types" \
    -- "model.json" "hardware.json"

check_list "~/.local/bin" "$(ls "$HOME/.local/bin" 2>/dev/null)" \
    "localllm.sh" "llama.sh" "model.sh" "server.sh" \
    -- \
    "${LOCAL_IGNORED[@]}"

# ~/.config/localllm: local.env, restorability-ignore and chat-types/ are in the backup; model.json is generated from
# the model profile (install/llama.sh, the llama-server service); the passphrase file (wherever local.env puts it) comes
# back by hand (README, reinstall step 11); hardware.json is a per-PC cache from ./localllm.sh monitor --detect-hw.
passphrase_name=""
[[ -n "$BACKUP_PASSPHRASE_FILE" && "$(dirname -- "$BACKUP_PASSPHRASE_FILE")" == "$(dirname -- "$LOCAL_ENV_FILE")" ]] \
    && passphrase_name="$(basename -- "$BACKUP_PASSPHRASE_FILE")"
browser_installer_name=""
[[ -n "$BROWSER_INSTALLER" && "$(dirname -- "$BROWSER_INSTALLER")" == "$(dirname -- "$LOCAL_ENV_FILE")" ]] \
    && browser_installer_name="$(basename -- "$BROWSER_INSTALLER")"
check_list "~/.config/localllm" "$(ls -A "$(dirname -- "$LOCAL_ENV_FILE")" 2>/dev/null)" \
    local.env restorability-ignore versions.env chat-types model.json search-gateway.json llama-api-key helper.env github-token \
    endpoints.json endpoints.env `# generated from local.env (lib/endpoints.py)` \
    ${passphrase_name:+"$passphrase_name"} ${browser_installer_name:+"$browser_installer_name"} \
    -- hardware.json
# The search gateway's state database (backup.sh) and its Python environment (install/search-gateway.sh).
# (Dated copies the two-PC switch keeps on purpose, *.moved-<date> and *.before-<date>, are not part of the setup.)
check_list "~/.local/share/localllm" "$(find "$(dirname -- "$SEARCH_GATEWAY_DB")" -mindepth 1 -maxdepth 1 -printf '%f\n' 2>/dev/null | grep -Ev '\.(moved|before)-[0-9-]+$')" \
    search-gateway.db search-gateway-venv \
    -- search-gateway.db-wal search-gateway.db-shm

check_list "user services" "$(find "$HOME/.config/systemd/user" -mindepth 1 -maxdepth 1 \( -name '*.service' -o -name '*.timer' \) -printf '%f\n' 2>/dev/null)" \
    open-webui.service search-gateway.service voice-router.service \
    localllm-backup.service localllm-backup.timer localllm-helper-keepalive.service localllm-helper-keepalive.timer \
    -- \
    pipewire-session-manager.service

if command -v nix >/dev/null; then
    check_list "Nix profile" "$(nix profile list 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g' | sed -nE 's/^Name: +//p')" \
        -- \
        "${LOCAL_IGNORED[@]}"
fi

# Containers started from this repo or ~/LocalLLM; others belong to other projects.
containers="$(docker_cmd ps -a --format '{{.Names}} {{.Label "com.docker.compose.project.working_dir"}}' 2>/dev/null \
    | awk -v r="$REPO_DIR" -v l="$LLM_HOME" 'index($2, r) == 1 || index($2, l) == 1 { print $1 }')"
check_list "Docker containers" "$containers" kokoro searxng-core searxng-valkey

system_files=(
    "$HOME/.config/environment.d/60-localllm-path.conf"
    /etc/systemd/system/localllm-llm.service
    /etc/systemd/zram-generator.conf
    /etc/sysctl.d/99-localllm-zram.conf
)
if [[ -n "${BROWSER_POLICY_DIR:-}" ]]; then
    system_files+=(
        "${BROWSER_POLICY_DIR%/}/recommended/localllm-memory.json"
        "${BROWSER_POLICY_DIR%/}/managed/localllm-autoplay.json"
    )
fi
for f in "${system_files[@]}"; do
    if [[ -f "$f" ]]; then ok "$f"; else bad "$f is missing: run the installer that writes it (install.sh)"; fi
done

echo
if [[ $FAIL -eq 0 ]]; then echo "Restorability: all covered."; else echo "Restorability: gaps found (FAIL above)."; fi
exit $FAIL
