#!/usr/bin/env bash
# backup-local.sh — copy the machine's own LocalLLM settings (~/.config/localllm, plus EXTRA_BACKUP_FILES) into backup/
# (kept out of git: it holds secrets), with this repo itself as backup/repo.bundle, and records the versions
# running now in ~/.config/localllm/versions.env (README, "Versions").
# archive-backup.sh and upload-backup.sh call it before packing backup/ into an encrypted archive.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/lib/common.sh"

BACKUP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/backup"
PI_FILES=(
  ~/.config/localllm/README.md
  ~/.config/localllm/local.env
  ~/.config/localllm/versions.env
  ~/.config/localllm/api.env
  ~/.config/localllm/chat-types.sh
)

copy_file() {
  local src="$1" dst="$2"
  if [[ -f "$src" ]]; then
    local dir; dir="$(dirname -- "$dst")"
    mkdir -p "$dir"
    cp -n "$src" "$dst"
    log_info "Copied $src -> $dst"
  else
    log_warn "Skipping missing file: $src"
  fi
}

copy_dir() {
  local src="$1" dst="$2"
  if [[ -d "$src" ]]; then
    mkdir -p "$dst"
    rsync -a --delete "$src/" "$dst"
    log_info "Copied directory $src -> $dst"
  else
    log_warn "Skipping missing directory: $src"
  fi
}

# snapshot_db <source .db> <destination>: a consistent copy of a SQLite database, safe while the app is running.
# .backup includes what is still in the WAL file; the copy is switched to a single file (no -wal/-shm next to it).
snapshot_db() {
  local src="$1" dst="$2"
  if [[ -f "$src" ]]; then
    mkdir -p "$(dirname -- "$dst")"
    sqlite3 "$src" ".backup '$dst'"
    log_info "Snapshot DB $src -> $dst"
  else
    log_warn "Skipping missing database: $src"
  fi
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

# --- Main ---

log_info "Starting LocalLLM backup to $BACKUP_DIR"

mkdir -p "$BACKUP_DIR"

# Copy PI files (user config)
for f in "${PI_FILES[@]}"; do
  copy_file "$f" "$BACKUP_DIR/$f"
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
