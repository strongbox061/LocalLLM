#!/usr/bin/env bash
# Refreshes backup/ and packs it into an encrypted archive: LocalLLM-backup-<date>.tar.gz.gpg.
#   ./archive-backup.sh [output folder]     default: BACKUP_ARCHIVE_DIR in ~/.config/localllm/local.env
# The passphrase is read from the file BACKUP_PASSPHRASE_FILE names in ~/.config/localllm/local.env (mode 600),
# when set; otherwise gpg asks for it. The output folder keeps only the newest BACKUP_KEEP_COUNT archives (local.env);
# older ones are deleted.
# The archive holds backup/ with the repo itself (backup/repo.bundle), so a fresh install needs nothing else.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="${HOME}/.config/localllm"
ENV_FILE="$CONFIG_DIR/local.env"

# Source environment defaults if available
[[ -f "$ENV_FILE" ]] && source "$ENV_FILE"

OUT_DIR="${1:-$BACKUP_ARCHIVE_DIR}"
[[ -n "$OUT_DIR" ]] || { echo "No archive folder: set BACKUP_ARCHIVE_DIR in $ENV_FILE" >&2; exit 1; }

[[ "$BACKUP_KEEP_COUNT" =~ ^[1-9][0-9]*$ ]] \
    || { echo "How many archives to keep isn't set: BACKUP_KEEP_COUNT in $ENV_FILE" >&2; exit 1; }

NAME="LocalLLM-backup-$(date +%Y-%m-%d-%H%M)"
TAR="$OUT_DIR/$NAME.tar.gz"
GPG_OUT="$TAR.gpg"

# Never leave an unencrypted or half-written archive behind.
trap 'rm -f "$TAR" "$GPG_OUT"' EXIT

# --- Helper: read passphrase from file if set ---
get_passphrase() {
    if [[ -n "${BACKUP_PASSPHRASE_FILE:-}" ]]; then
        [[ -f "$BACKUP_PASSPHRASE_FILE" ]] \
            || { echo "BACKUP_PASSPHRASE_FILE in $ENV_FILE names a file that doesn't exist." >&2; exit 1; }
        local perms
        perms=$(stat -c %a "$BACKUP_PASSPHRASE_FILE")
        [[ "$perms" == "600" ]] \
            || { echo "$BACKUP_PASSPHRASE_FILE can be read by others: chmod 600 it." >&2; exit 1; }
        cat "$BACKUP_PASSPHRASE_FILE"
    else
        return 1
    fi
}

# --- Create backup directory and bundle the repo ---
create_backup() {
    local backup_dir="backup"
    rm -rf "$backup_dir"
    mkdir -p "$backup_dir"

    # Bundle the repository (exclude backup/ itself, .git, and large binaries)
    tar czf "$TAR" \
        --exclude='backup/' \
        --exclude='.git' \
        --exclude='*.bin' \
        --exclude='models/' \
        .

    echo "Archive created: $TAR"
}

# --- Encrypt the archive ---
encrypt_archive() {
    local passphrase=""
    passphrase="$(get_passphrase)" || true

    if [[ -n "$passphrase" ]]; then
        gpg --batch --yes --passphrase "$passphrase" \
            --symmetric --cipher-algo AES256 \
            --output "$GPG_OUT" "$TAR"
    else
        gpg --batch --yes --symmetric \
            --cipher-algo AES256 \
            --output "$GPG_OUT" "$TAR"
    fi

    echo "Encrypted: $GPG_OUT"
}

# --- Clean up old archives ---
cleanup_old_archives() {
    local keep="${BACKUP_KEEP_COUNT:-3}"
    local count

    # List archives sorted by name (date prefix ensures chronological order)
    count=$(ls -1 "$OUT_DIR"/LocalLLM-backup-*.tar.gz.gpg 2>/dev/null | wc -l)

    if (( count > keep )); then
        local to_remove=$(( count - keep ))
        ls -1 "$OUT_DIR"/LocalLLM-backup-*.tar.gz.gpg | sort | head -n "$to_remove" | while read -r old_archive; do
            rm -f "$old_archive"
            echo "Removed old archive: $old_archive"
        done
    fi
}

# --- Main ---
main() {
    # Ensure output directory exists
    mkdir -p "$OUT_DIR"

    create_backup
    encrypt_archive

    # Remove trap since we succeeded
    trap - EXIT

    cleanup_old_archives

    echo "$GPG_OUT"
}

main "$@"
