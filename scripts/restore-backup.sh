#!/usr/bin/env bash
# restore-backup.sh — restore LocalLLM settings and data from a backup directory
# (~/.config/localllm, EXTRA_BACKUP_FILES) read them.
#   ./restore.sh                 overwrite; anything that differs is first kept as <name>.bak.<timestamp>
#   ./restore.sh --missing-only  only restore what does not exist yet (what install.sh uses)
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/lib/common.sh"

MISSING_ONLY=false
case "${1:-}" in
    "") ;;
    "--missing-only") MISSING_ONLY=true ;;
    *) echo "Usage: $0 [--missing-only]"; exit 1 ;;
esac

STAMP="$(date +%Y%m%d-%H%M%S)"

# Runs a command with sudo when the target directory is not writable (SearXNG's container owns its config).
as_owner() {
    local dir="$1"; shift
    if [ ! -w "$dir" ]; then
        sudo "$@"
    else
        "$@"
    fi
}

restore_file() {
    # restore_file <backup file> <target> [mode]
    local src="$1" dst="$2" mode="${3:-}"

    if [ "$MISSING_ONLY" = true ] && [ -f "$dst" ]; then
        echo "SKIP (exists): $dst"
        return 0
    fi

    if [ ! -f "$src" ]; then
        echo "MISSING: $src -> $dst"
        return 0
    fi

    if [ -f "$dst" ] && ! cmp -s "$src" "$dst"; then
        cp -p "$dst" "${dst}.bak.${STAMP}"
        echo "BACKED UP: ${dst}.bak.${STAMP}"
    fi

    local dst_dir
    dst_dir="$(dirname "$dst")"
    mkdir -p "$dst_dir"
    as_owner "$dst_dir" cp -p "$src" "$dst"

    if [ -n "$mode" ]; then
        as_owner "$dst_dir" chmod "$mode" "$dst"
    fi

    echo "RESTORED: $src -> $dst"
}

restore_dir() {
    # restore_dir <backup directory> <target>
    local src="$1" dst="$2"

    if [ "$MISSING_ONLY" = true ] && [ -d "$dst" ]; then
        echo "SKIP (exists): $dst"
        return 0
    fi

    if [ ! -d "$src" ]; then
        echo "MISSING: $src -> $dst"
        return 0
    fi

    local dst_dir
    dst_dir="$(dirname "$dst")"
    mkdir -p "$dst_dir"
    as_owner "$dst_dir" cp -a "$src"/. "$dst"/

    echo "RESTORED DIR: $src -> $dst"
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

# A backup from before the search gateway had its own database keeps its usage counts and search log next to Open
# WebUI's (search_gateway_* or, before the rename, web_research_*): they go back there, and install/search-gateway.sh
# moves them into the gateway's database.
WEBUI_ELSEWHERE=false
