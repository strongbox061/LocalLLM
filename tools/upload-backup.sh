#!/usr/bin/env bash
# Makes an encrypted backup archive (archive-backup.sh) and uploads it with rclone to BACKUP_REMOTE (remote:folder,
# set in ~/.config/localllm/local.env). Run by the localllm-backup timer every 6 hours; can also be run by hand.
# The remote keeps only the newest BACKUP_KEEP_COUNT archives (local.env); older ones are deleted only after a
# successful upload. On failure: a desktop notification.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
source "$REPO_DIR/.env" 2>/dev/null || true

DEST="$BACKUP_REMOTE"

fail() {
    notify-send "LocalLLM Backup Failed" "$1" 2>/dev/null || true
    exit 1
}

# Create the encrypted archive via archive-backup.sh; it prints the filename on stdout.
ARCHIVE="$("$REPO_DIR/archive-backup.sh" | tail -1)"

if [ -z "$ARCHIVE" ]; then
    fail "archive-backup.sh produced no output"
fi

if [ ! -f "$ARCHIVE" ]; then
    fail "archive file not found: $ARCHIVE"
fi

# Upload to the remote.
rclone copy "$ARCHIVE" "$DEST/" || fail "rclone upload failed for $ARCHIVE"

# Remove old archives, keeping only the newest BACKUP_KEEP_COUNT.
COUNT=$(rclone ls "$DEST" --files-only | wc -l)
KEEP="${BACKUP_KEEP_COUNT:-3}"
if [ "$COUNT" -gt "$KEEP" ]; then
    TO_DELETE=$((COUNT - KEEP))
    rclone ls "$DEST" --files-only --sort=-name | tail -n "$TO_DELETE" | awk '{print $2}' | while read -r old; do
        rclone delete "$old" || true
    done
fi
