#!/usr/bin/env bash
# machine's own LocalLLM settings (~/.config/localllm, plus EXTRA_BACKUP_FILES) into backup/ (kept out of git: it holds
# secrets), with this repo itself as backup/repo.bundle, and records the versions running now in
# ~/.config/localllm/versions.env (README, "Versions").
# Safe while Open WebUI runs: its databases are copied as SQLite snapshots.
# archive-backup.sh and upload-backup.sh call it before packing backup/ into an encrypted archive.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/lib/common.sh"

PI_FILES=(
    settings.json
    models.json
    sandbox.json
    auth.json
    models-store.json
    tool-view-mode.json
    mcp-onboarding.json
)

copy_file() {   # copy_file <source> <destination>
    if [[ -f "$1" ]]; then
        mkdir -p "$(dirname -- "$2")"
        cp "$1" "$2"
        echo "  saved   ${2#"$REPO_DIR"/}"
    else
        echo "  missing $1"
    fi
}

copy_dir() {    # copy_dir <source> <destination>, replacing the destination
    if [[ -d "$1" ]]; then
        rm -rf "$2"
        mkdir -p "$(dirname -- "$2")"
        cp -r "$1" "$2"
        echo "  saved   ${2#"$REPO_DIR"/}/"
    else
        echo "  missing $1"
    fi
}

# snapshot_db <source .db> <destination>: a consistent copy of a SQLite database, safe while the app is running.
# .backup includes what is still in the WAL file; the copy is switched to a single file (no -wal/-shm next to it).
snapshot_db() {
    local src="$1" dst="$2" tmp="$2.tmp"
    if [[ ! -f "$src" ]]; then
        echo "  missing $src"
        return 0
    fi
    mkdir -p "$(dirname -- "$dst")"
    rm -f "$tmp"
    sqlite3 "$src" ".backup '$tmp'"
    [[ "$(sqlite3 "$tmp" 'pragma integrity_check;')" == "ok" ]] || die "The snapshot of $src is corrupt: $tmp"
    sqlite3 "$tmp" 'pragma journal_mode=delete;' >/dev/null
    rm -f "$dst" "$dst-wal" "$dst-shm"
    mv "$tmp" "$dst"
    echo "  saved   ${dst#"$REPO_DIR"/}"
}

log "Pi ($PI_AGENT_DIR)"
for f in "${PI_FILES[@]}"; do
    copy_file "$PI_AGENT_DIR/$f" "$BACKUP_DIR/pi/$f"
done
# The encrypted Context7 sign-in (its key is in local.env, saved below).
copy_dir "$PI_AGENT_DIR/mcp-oauth-encrypted" "$BACKUP_DIR/pi/mcp-oauth-encrypted"

# write_versions <file>: the versions running now, so a fresh install from this backup gets the same ones
# (lib/common.sh reads them; the installers use them only for what they install new).
write_versions() {
    local out="$1" v img
    {
        echo "# Versions running at the last backup ($(date +%F)). Written by backup.sh; don't edit."
        v="$(command -v "$PI_AGENT_DIR" &>/dev/null && head -1 "$PI_AGENT_DIR/VERSION" 2>/dev/null || echo '')"
        [[ -n "$v" ]] && echo "PI_VERSION=\"$v\""
        v="$(python3 - "$PI_AGENT_DIR/npm" <<'PY'
import json, os, sys
root = sys.argv[1]
try:
    deps = json.load(open(os.path.join(root, "package.json"))).get("dependencies", {})
except OSError:
    deps = {}
out = []
for name in sorted(deps):
    try:
        out.append(name + "@" + json.load(open(os.path.join(root, "node_modules", name, "package.json")))["version"])
    except (OSError, KeyError):
        pass
print(" ".join(out))
PY
)"
        [[ -n "$v" ]] && echo "PI_PACKAGE_VERSIONS=\"$v\""
        v="$(uv tool list 2>/dev/null | sed -nE 's/^open-webui v([0-9][^ ]*).*/\1/p')"
        [[ -n "$v" ]] && echo "OPEN_WEBUI_VERSION=\"$v\""
        v="$(git -C "$LLM_HOME/llama.cpp" rev-parse HEAD 2>/dev/null)"
        [[ -n "$v" ]] && echo "LLAMA_CPP_COMMIT=\"$v\""
        for pair in SEARXNG_IMAGE:searxng-core VALKEY_IMAGE:searxng-valkey; do
            img="$(docker_cmd inspect --format '{{.Image}}' "${pair#*:}" 2>/dev/null)" || continue
            v="$(docker_cmd image inspect --format '{{index .RepoDigests 0}}' "$img" 2>/dev/null)" || continue
            [[ -n "$v" ]] && echo "${pair%%:*}=\"$v\""
        done
    } > "$out.tmp"
    # A value that couldn't be read now (e.g. Docker needs sudo in the background) keeps the one recorded before.
    if [[ -f "$out" ]]; then
        local kept="" line
        while IFS= read -r line; do
            grep -q "^${line%%=*}=" "$out.tmp" || kept+="$line"$'\n'
        done < <(grep -E '^[A-Z_]+=' "$out")
        printf '%s' "$kept" >> "$out.tmp"
    fi
    mv "$out.tmp" "$out"
}

# Snapshot SQLite databases if present
if [[ -f ~/.config/localllm/chat_history.db ]]; then
  snapshot_db ~/.config/localllm/chat_history.db "$BACKUP_DIR/chat_history.db"
fi

# Bundle the repo itself
if [[ -d "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/.git" ]]; then
  log_info "Bundling repository..."
  git bundle create "$BACKUP_DIR/repo.bundle" --all
else
  log_warn "Not a git repo — skipping repo bundle"
fi

# Write version info
write_versions "$BACKUP_DIR/versions.env"

log_info "Backup complete: $BACKUP_DIR"
