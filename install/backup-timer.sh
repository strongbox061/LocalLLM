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
  --enable) ENABLE=true ;;
  *) ;;
esac

# Install rclone if it is not already available
if ! command -v rclone &>/dev/null; then
  log_info "Installing rclone..."
  curl -fsSL https://rclone.org/install.sh | sudo bash
fi

# Read the backup remote from the environment file
BACKUP_REMOTE=""
if [[ -f "$CONFIG_DIR/local.env" ]]; then
  BACKUP_REMOTE=$(grep '^BACKUP_REMOTE=' "$CONFIG_DIR/local.env" | head -1 | cut -d= -f2-)
fi

if [[ -z "$BACKUP_REMOTE" ]]; then
  log_warn "BACKUP_REMOTE not set in $CONFIG_DIR/local.env — timer will be installed but needs rclone config first"
fi

# Create systemd unit files
UNIT_DIR="/etc/systemd/system"

cat > "${UNIT_DIR}/localllm-backup.service" <<'EOF'
[Unit]
Description=LocalLLM encrypted backup upload
After=network-online.target

[Service]
Type=oneshot
EnvironmentFile=${CONFIG_HOME:-$HOME/.config/localllm}/local.env
ExecStart=/usr/local/bin/upload-backup.sh
EOF

cat > "${UNIT_DIR}/localllm-backup.timer" <<'EOF'
[Unit]
Description=Run LocalLLM backup every 6 hours

[Timer]
OnBootSec=30min
OnUnitActiveSec=6h

[Install]
WantedBy=timers.target
EOF

# Reload systemd and install the timer
sudo systemctl daemon-reload
sudo systemctl enable localllm-backup.timer

log_info "localllm-backup.timer installed (every 6 hours)."
log_info ""
log_info "To enable the timer and run a test upload:"
log_info "  sudo install/backup-timer.sh --enable"
log_info ""
log_info "Before that, make sure you have:"
log_info "  1. rclone configured for your remote (rclone config)"
log_info "  2. A GPG passphrase file at ~/.config/localllm/gpg-passphrase"

if [[ "$ENABLE" == true ]]; then
  if [[ -n "$BACKUP_REMOTE" ]]; then
    log_info "Running test upload..."
    /usr/local/bin/upload-backup.sh && log_info "Test upload succeeded." || log_warn "Test upload failed — check rclone config and passphrase file."
  else
    log_warn "Skipping test upload: BACKUP_REMOTE is not set."
  fi
fi
