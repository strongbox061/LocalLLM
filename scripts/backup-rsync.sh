#!/usr/bin/env bash
set -euo pipefail

# backup-rsync.sh — Sync local LLM artifacts to a backup directory via rsync.
# Usage: ./scripts/backup-rsync.sh [BACKUP_DIR]

BACKUP_DIR="${1:-${BACKUP_DIR:-$(dirname "$(realpath "$0")")/../backups/llm-artifacts}}"
SOURCE_DIR="$(dirname "$(realpath "$0")")"
DRY_RUN="${DRY_RUN:-}"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  echo "Usage: $0 [BACKUP_DIR]"
  echo ""
  echo "Options:"
  echo "  BACKUP_DIR        Destination for rsync (default: ./backups/llm-artifacts)"
  echo ""
  echo "Environment variables:"
  echo "  BACKUP_DIR        Override backup destination"
  echo "  DRY_RUN           Set to '1' to preview changes without executing"
  exit 0
fi

mkdir -p "${BACKUP_DIR}"

RSYNC_OPTS="--archive --delete --human-readable --progress"
if [[ -n "${DRY_RUN}" ]]; then
  RSYNC_OPTS+=" --dry-run"
  echo "[DRY RUN] Would rsync from ${SOURCE_DIR} to ${BACKUP_DIR}"
fi

rsync ${RSYNC_OPTS} \
  "${SOURCE_DIR}/../models/" \
  "${SOURCE_DIR}/../scripts/" \
  "${SOURCE_DIR}/../llama.cpp/" \
  "${BACKUP_DIR}/"

echo "Backup complete: ${BACKUP_DIR}"
