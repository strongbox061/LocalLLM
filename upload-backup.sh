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
rclone listremotes | grep -qxF "${DEST%%:*}:" \
    || { echo "No rclone remote '${DEST%%:*}': run 'rclone config' (see README)." >&2; false; }
[[ "$BACKUP_KEEP_COUNT" =~ ^[1-9][0-9]*$ ]] \
    || { echo "How many archives to keep isn't set: BACKUP_KEEP_COUNT in $LOCAL_ENV_FILE (see README)." >&2; false; }
[[ -n "$BACKUP_PASSPHRASE_FILE" && -f "$BACKUP_PASSPHRASE_FILE" ]] \
    || { echo "No passphrase file set in $LOCAL_ENV_FILE (BACKUP_PASSPHRASE_FILE), or it doesn't exist: an unattended" \
              "backup cannot ask for the passphrase (see README, \"Backing up and restoring settings\")." >&2; false; }

step="Making the archive"
# Show its output, through stderr: not `tee /dev/stderr`, which can't be opened under systemd (stderr is a socket).
ARCHIVE="$("$REPO_DIR/archive-backup.sh" | tee >(cat >&2) | tail -1)"
[[ -f "$ARCHIVE" ]]

step="Uploading"
log "Upload to $DEST"
rclone copy "$ARCHIVE" "$DEST/"

step="Removing old archives from the remote"
# All but the newest BACKUP_KEEP_COUNT (the names sort by date; the one just uploaded is the newest).
rclone lsf --files-only --include 'LocalLLM-backup-*.tar.gz.gpg' "$DEST/" | sort | head -n -"$BACKUP_KEEP_COUNT" \
    | while IFS= read -r old; do rclone deletefile "$DEST/$old"; echo "  deleted $DEST/$old"; done

echo "Uploaded $(basename -- "$ARCHIVE") ($(du -h "$ARCHIVE" | cut -f1)) to $DEST"
