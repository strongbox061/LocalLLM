#!/usr/bin/env bash
# Refreshes backup/ and packs it into an encrypted archive: LocalLLM-backup-<date>.tar.gz.gpg.
#   ./archive-backup.sh [output folder]     default: BACKUP_ARCHIVE_DIR in ~/.config/localllm/local.env
# The passphrase is read from the file BACKUP_PASSPHRASE_FILE names in ~/.config/localllm/local.env (mode 600),
# when set; otherwise gpg asks for it. The output folder keeps only the newest BACKUP_KEEP_COUNT archives (local.env);
# older ones are deleted.
# The archive holds backup/ with the repo itself (backup/repo.bundle), so a fresh install needs nothing else.
# The last line printed is the archive's path. Restore: README, "Opening a backup".
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/lib/common.sh"

OUT_DIR="${1:-$BACKUP_ARCHIVE_DIR}"
[[ -n "$OUT_DIR" ]] || die "No archive folder: set BACKUP_ARCHIVE_DIR in $LOCAL_ENV_FILE (see config/local.example.env), or pass one."
[[ "$BACKUP_KEEP_COUNT" =~ ^[1-9][0-9]*$ ]] \
    || die "How many archives to keep isn't set: BACKUP_KEEP_COUNT in $LOCAL_ENV_FILE (see config/local.example.env)."

if [[ -n "$BACKUP_PASSPHRASE_FILE" ]]; then
    [[ -f "$BACKUP_PASSPHRASE_FILE" ]] || die "BACKUP_PASSPHRASE_FILE in $LOCAL_ENV_FILE names a file that doesn't exist."
    [[ "$(stat -c %a "$BACKUP_PASSPHRASE_FILE")" == "600" ]] \
        || die "$BACKUP_PASSPHRASE_FILE can be read by others: chmod 600 it."
    GPG_PASS=(--batch --pinentry-mode loopback --passphrase-file "$BACKUP_PASSPHRASE_FILE")
else
    GPG_PASS=()
fi

"$REPO_DIR/backup.sh"

log "Archive"
mkdir -p "$OUT_DIR"
NAME="LocalLLM-backup-$(date +%Y-%m-%d-%H%M)"
TAR="$OUT_DIR/$NAME.tar.gz"
GPG_OUT="$TAR.gpg"
# Never leave an unencrypted or half-written archive behind.
trap 'rm -f "$TAR" "$GPG_OUT.part"' EXIT

tar -czf "$TAR" -C "$REPO_DIR" backup
gpg --quiet --symmetric --cipher-algo AES256 "${GPG_PASS[@]}" --output "$GPG_OUT.part" "$TAR"
mv "$GPG_OUT.part" "$GPG_OUT"
rm -f "$TAR"

# All but the newest BACKUP_KEEP_COUNT (the names sort by date; the archive just made is the newest, so it stays).
find "$OUT_DIR" -maxdepth 1 -name 'LocalLLM-backup-*.tar.gz.gpg' -printf '%f\n' | sort | head -n -"$BACKUP_KEEP_COUNT" \
    | while IFS= read -r old; do rm -f "$OUT_DIR/$old"; echo "  deleted $OUT_DIR/$old"; done
echo "  saved   $GPG_OUT ($(du -h "$GPG_OUT" | cut -f1))"
echo "$GPG_OUT"
