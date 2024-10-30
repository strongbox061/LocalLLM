#!/usr/bin/env bash
# Makes an encrypted backup archive (archive-backup.sh) and uploads it with rclone to BACKUP_REMOTE (remote:folder,
# set in ~/.config/localllm/local.env). Run by the localllm-backup timer every 6 hours; can also be run by hand.
# The remote keeps only the newest BACKUP_KEEP_COUNT archives (local.env); older ones are deleted only after a
# successful upload. On failure: a desktop notification.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/lib/common.sh"

DEST="$BACKUP_REMOTE"
step="start"

fail() {
    local msg="$step failed. Log: journalctl --user -u localllm-backup"
    command -v notify-send >/dev/null && notify-send -u critical -a LocalLLM "LocalLLM backup failed" "$msg" || true
    printf 'ERROR: %s\n' "$msg" >&2
}
trap fail ERR

step="checks"
command -v rclone >/dev/null || { echo "rclone is not installed: run install/backup-timer.sh" >&2; false; }
[[ "$DEST" == ?*:* ]] \
    || { echo "No upload destination: set BACKUP_REMOTE=\"remote:folder\" in $LOCAL_ENV_FILE (see README)." >&2; false; }
[[ "$BACKUP_KEEP_COUNT" =~ ^[1-9][0-9]*$ ]] \
    || { echo "How many archives to keep isn't set: BACKUP_KEEP_COUNT in $LOCAL_ENV_FILE (see README)." >&2; false; }
[[ -n "$BACKUP_PASSPHRASE_FILE" && -f "$BACKUP_PASSPHRASE_FILE" ]] \
    || { echo "No passphrase file set in $LOCAL_ENV_FILE (BACKUP_PASSPHRASE_FILE), or it doesn't exist: an unattended" \
              "backup cannot ask for the passphrase (see README, \"Backing up and restoring settings\")." >&2; false; }

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
