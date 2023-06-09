#!/usr/bin/env bash
# Installs rclone and the localllm-backup timer, which uploads an encrypted backup with rclone every 6 hours (to
# BACKUP_REMOTE in ~/.config/localllm/local.env).
#   install/backup-timer.sh            installs the units; lists what's still needed to turn the timer on
#   install/backup-timer.sh --enable   also, once rclone and the passphrase file are set up: one test upload
#                                      (upload-backup.sh), then turns the timer on
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

ENABLE=false
case "${1:-}" in
    "") ;;
    --enable) ENABLE=true ;;
    *) die "Usage: $0 [--enable]" ;;
esac

command -v rclone >/dev/null || sudo pacman -S --needed rclone

render() { sed "s|@REPO_DIR@|$REPO_DIR|" "$REPO_DIR/services/$1"; }
install_user_unit localllm-backup.service "$(render localllm-backup.service)"
install_user_unit localllm-backup.timer "$(render localllm-backup.timer)"

todo=()
if [[ "$BACKUP_REMOTE" != ?*:* ]]; then
    todo+=("set BACKUP_REMOTE=\"remote:folder\" in $LOCAL_ENV_FILE (see config/local.example.env)")
elif ! rclone listremotes | grep -qxF "${BACKUP_REMOTE%%:*}:"; then
    todo+=("rclone config    (create the remote '${BACKUP_REMOTE%%:*}' that BACKUP_REMOTE names, with your storage's login)")
fi
[[ "$BACKUP_KEEP_COUNT" =~ ^[1-9][0-9]*$ ]] \
    || todo+=("set BACKUP_KEEP_COUNT (how many archives to keep, e.g. 8) in $LOCAL_ENV_FILE (see config/local.example.env)")
if [[ ${#todo[@]} -eq 0 ]] && ! systemctl --user is-enabled --quiet localllm-backup.timer; then
    if $ENABLE; then
        echo "Test upload before turning the timer on:"
        "$REPO_DIR/upload-backup.sh"
        systemctl --user enable --now localllm-backup.timer
    else
        todo+=("install/backup-timer.sh --enable    (one test upload, then turns the timer on)")
    fi
fi

if [[ ${#todo[@]} -eq 0 ]]; then
    echo "Backup timer enabled; next run:"
    systemctl --user list-timers localllm-backup.timer --no-pager | sed -n 2p
else
    echo "To turn on the automatic backup, still to do:"
    printf '  - %s\n' "${todo[@]}"
fi
