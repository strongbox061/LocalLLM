#!/usr/bin/env bash
# restore.sh — restore LocalLLM settings and data from a backup directory
# (~/.config/localllm, EXTRA_BACKUP_FILES) read them.
#   ./restore.sh                 overwrite; anything that differs is first kept as <name>.bak.<timestamp>
#   ./restore.sh --missing-only  only restore what does not exist yet (what install.sh uses)
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/lib/common.sh"

MISSING_ONLY=false
case "${1:-}" in
    "") ;;
    --missing-only) MISSING_ONLY=true ;;
    *) die "Usage: $0 [--missing-only]" ;;
esac

STAMP="$(date +%Y%m%d-%H%M%S)"

# Runs a command with sudo when the target directory is not writable (SearXNG's container owns its config).
as_owner() {    # as_owner <directory> <command...>
    local dir="$1"
    shift
    while [[ ! -e "$dir" ]]; do dir="$(dirname -- "$dir")"; done
    if [[ -w "$dir" ]]; then "$@"; else sudo "$@"; fi
}

restore_file() {   # restore_file <backup file> <target> [mode]
    local src="$1" dst="$2" mode="${3:-}" dir
    dir="$(dirname -- "$dst")"
    [[ -f "$src" ]] || return 0
    if [[ -f "$dst" ]]; then
        if cmp -s "$src" "$dst"; then
            echo "  same    $dst"
            return 0
        fi
        if $MISSING_ONLY; then
            echo "  kept    $dst (differs from the backup)"
            return 0
        fi
        as_owner "$dir" cp -a "$dst" "$dst.bak.$STAMP"
        echo "  old one $dst.bak.$STAMP"
    fi
    as_owner "$dir" mkdir -p "$dir"
    as_owner "$dir" cp "$src" "$dst"
    [[ -z "$mode" ]] || as_owner "$dir" chmod "$mode" "$dst"
    echo "  restored $dst"
}

restore_dir() {    # restore_dir <backup directory> <target>
    local src="$1" dst="$2"
    [[ -d "$src" ]] || return 0
    if [[ -d "$dst" ]] && diff -rq "$src" "$dst" >/dev/null 2>&1; then
        echo "  same    $dst/"
        return 0
    fi
    if [[ -d "$dst" ]] && [[ -n "$(ls -A "$dst")" ]]; then
        if $MISSING_ONLY; then
            echo "  kept    $dst/ (differs from the backup)"
            return 0
        fi
        mv "$dst" "$dst.bak.$STAMP"
        echo "  old one $dst.bak.$STAMP/"
    fi
    rm -rf "$dst"
    mkdir -p "$(dirname -- "$dst")"
    cp -r "$src" "$dst"
    echo "  restored $dst/"
}

# so this PC starts in single mode; ./localllm.sh switch split moves it to the helper again (README, "Rebuilding both
# PCs").
FROM_SPLIT=false

# The two-PC setup: llama-server's API key, the helper PC's values, the SSH key to it and its fingerprint. The Host
# block in ~/.ssh/config is written again by ./localllm.sh setup-helper (from HELPER_HOST/HELPER_USER in local.env).
# Extra files (EXTRA_BACKUP_FILES in local.env): back to the same place in your home folder.
GW_SRC="$BACKUP_DIR/search-gateway/search-gateway.db"
DB_DST="$WEBUI_DATA_DIR/webui.db"
DB_RESTORED=false

[[ -d "$BACKUP_DIR" ]] && [[ -n "$(ls -A "$BACKUP_DIR")" ]] || { echo "Nothing in $BACKUP_DIR, nothing to restore."; exit 0; }

log "Pi ($PI_AGENT_DIR)"
restore_file "$BACKUP_DIR/pi/settings.json"          "$PI_AGENT_DIR/settings.json"
restore_file "$BACKUP_DIR/pi/models.json"            "$PI_AGENT_DIR/models.json" 600
restore_file "$BACKUP_DIR/pi/sandbox.json"           "$PI_AGENT_DIR/sandbox.json"
restore_file "$BACKUP_DIR/pi/auth.json"              "$PI_AGENT_DIR/auth.json" 600
restore_file "$BACKUP_DIR/pi/models-store.json"      "$PI_AGENT_DIR/models-store.json" 600
restore_file "$BACKUP_DIR/pi/tool-view-mode.json"    "$PI_AGENT_DIR/tool-view-mode.json" 600
restore_file "$BACKUP_DIR/pi/mcp-onboarding.json"    "$PI_AGENT_DIR/mcp-onboarding.json"
restore_dir  "$BACKUP_DIR/pi/mcp-oauth-encrypted"    "$PI_AGENT_DIR/mcp-oauth-encrypted"

log "This kit's own settings (~/.config/localllm)"
had_local_env=false
[[ -f "$LOCAL_ENV_FILE" ]] && had_local_env=true
restore_file "$BACKUP_DIR/localllm/local.env" "$LOCAL_ENV_FILE" 600
# A backup made in split mode, on a PC that had no settings yet (a rebuild): Open WebUI's data comes back here (below),
# so this PC starts in single mode; ./localllm.sh switch split moves it to the helper again (README, "Rebuilding both
# PCs").
FROM_SPLIT=false
if ! $had_local_env && grep -qE '^LOCALLLM_MODE=["'"'"']?split' "$LOCAL_ENV_FILE" 2>/dev/null; then
    sed -i -E 's/^LOCALLLM_MODE=.*/LOCALLLM_MODE=single/' "$LOCAL_ENV_FILE"
    FROM_SPLIT=true
    echo "  set     LOCALLLM_MODE=single (the backup was made in split mode; Open WebUI's data is restored here)"
fi
# A backup made while Open WebUI was toggled off (LOCALLLM_APPS=pi), on a fresh PC: enable owui so
# ./start.sh starts Open WebUI here and ./localllm.sh switch split can move its data to the helper.
if ! $had_local_env && [[ -f "$BACKUP_DIR/open-webui/webui.db" ]] \
    && grep -qE '^LOCALLLM_APPS=["'"'"']?pi["'"'"']?$' "$LOCAL_ENV_FILE" 2>/dev/null; then
    sed -i -E 's/^LOCALLLM_APPS=.*/LOCALLLM_APPS=pi,owui/' "$LOCAL_ENV_FILE"
    echo "  set     LOCALLLM_APPS=pi,owui (so Open WebUI starts here and can move to the helper)"
fi
restore_file "$BACKUP_DIR/localllm/restorability-ignore" "$RESTORABILITY_IGNORE_FILE"
restore_file "$BACKUP_DIR/localllm/search-gateway.json" "$SEARCH_GATEWAY_SETTINGS" 600
restore_dir  "$BACKUP_DIR/localllm/chat-types" "$CHAT_TYPES_DIR"
restore_file "$BACKUP_DIR/localllm/versions.env" "$VERSIONS_FILE"
restore_file "$BACKUP_DIR/localllm/github-token" "$GITHUB_TOKEN_FILE" 600
# The two-PC setup: llama-server's API key, the helper PC's values, the SSH key to it and its fingerprint. The Host
# block in ~/.ssh/config is written again by ./localllm.sh setup-helper (from HELPER_HOST/HELPER_USER in local.env).
restore_file "$BACKUP_DIR/localllm/llama-api-key" "$LLAMA_API_KEY_FILE" 600
restore_file "$BACKUP_DIR/localllm/helper.env" "$HELPER_ENV_FILE" 600
if [[ -d "$BACKUP_DIR/localllm/ssh" ]]; then
    mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
    restore_file "$BACKUP_DIR/localllm/ssh/$(basename -- "$HELPER_SSH_KEY")" "$HELPER_SSH_KEY" 600
    restore_file "$BACKUP_DIR/localllm/ssh/$(basename -- "$HELPER_SSH_KEY").pub" "$HELPER_SSH_KEY.pub" 644
    # The helper's fingerprint: its lines are added to known_hosts (the rest of the file stays).
    if [[ -f "$BACKUP_DIR/localllm/ssh/known_hosts" ]]; then
        added=0
        while IFS= read -r line; do
            [[ -n "$line" ]] || continue
            grep -qxF -- "$line" "$HOME/.ssh/known_hosts" 2>/dev/null && continue
            printf '%s\n' "$line" >> "$HOME/.ssh/known_hosts"
            added=$((added + 1))
        done < "$BACKUP_DIR/localllm/ssh/known_hosts"
        chmod 600 "$HOME/.ssh/known_hosts"
        if [[ $added -gt 0 ]]; then echo "  restored the helper PC's fingerprint in ~/.ssh/known_hosts"; else echo "  same    ~/.ssh/known_hosts (the helper PC's fingerprint)"; fi
    fi
fi
# Extra files (EXTRA_BACKUP_FILES in local.env): back to the same place in your home folder.
if [[ -d "$BACKUP_DIR/extra" ]]; then
    while IFS= read -r -d '' f; do
        target="$HOME/${f#"$BACKUP_DIR/extra/"}"
        if [[ "$target" == */.git/info/exclude ]]; then
            extra_repo="${target%/.git/info/exclude}"
            if [[ "$extra_repo" != "$REPO_DIR" ]]; then
                warn "EXTRA_BACKUP_FILES targets $extra_repo, but this repo is at $REPO_DIR: clone into $extra_repo so repo extras land inside it."
            fi
        fi
        # A file with nothing but comments (like the .git/info/exclude a fresh clone gets) counts as missing.
        if [[ -f "$target" ]] && ! grep -qv -E '^[[:space:]]*(#|$)' "$target"; then
            rm -f "$target"
        fi
        restore_file "$f" "$target"
    done < <(find "$BACKUP_DIR/extra" -type f -print0)
fi
