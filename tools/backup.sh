#!/usr/bin/env bash
# tools/backup.sh — copy the machine's own LocalLLM settings (~/.config/localllm, plus EXTRA_BACKUP_FILES) into backup/
# (kept out of git: it holds secrets), with this repo itself as backup/repo.bundle, and records the versions
# running now in ~/.config/localllm/versions.env (README, "Versions").
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/lib/common.sh"

BACKUP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/backup"
LOCALLLM_DIR=~/.config/localllm
LOCALLLM_FILES=(
    settings.json
    models.json
    sandbox.json
    auth.json
    models-store.json
    tool-view-mode.json
)

# copy_file <source> <destination>
copy_file() {
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

# write_versions <file>: the versions running now, so a fresh install from this backup gets the same ones
# (lib/common.sh reads them; the installers use them only for what they install new).
write_versions() {
  local file="$1"
  mkdir -p "$(dirname -- "$file")"

  local llama_version="unknown"
  if command -v llama-server &>/dev/null; then
    llama_version="$(llama-server --version 2>/dev/null || echo unknown)"
  elif command -v main &>/dev/null; then
    llama_version="$(main --version 2>/dev/null || echo unknown)"
  fi

  local vicuna_version="unknown"
  if [[ -f ~/.config/localllm/vicuna-7b.version ]]; then
    vicuna_version="$(cat ~/.config/localllm/vicuna-7b.version)"
  fi

  cat > "$file" <<EOF
LLAMA_CPP_VERSION=${llama_version}
VICUNA_MODEL_VERSION=${vicuna_version}
BACKUP_DATE=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
EOF
  log_info "Wrote versions to $file"
}

log "LocalLLM config ($LOCALLLM_DIR)"
for f in "${LOCALLLM_FILES[@]}"; do
    copy_file "$LOCALLLM_DIR/$f" "$BACKUP_DIR/pi/$f"
done

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
