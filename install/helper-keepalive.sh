#!/usr/bin/env bash
# Installs the localllm-helper-keepalive user timer, which pings the helper PC over Tailscale every 60 seconds so
# the direct WireGuard path stays warm and SSH doesn't time out after idle periods.
#   install/helper-keepalive.sh            installs the units (requires HELPER_HOST in local.env)
#   install/helper-keepalive.sh --enable   installs and enables the timer
#   install/helper-keepalive.sh --disable  stops and disables the timer
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

ACTION=""
case "${1:-}" in
    "") ;;
    --enable) ACTION=enable ;;
    --disable) ACTION=disable ;;
    *) die "Usage: $0 [--enable|--disable]" ;;
esac

if [[ "$ACTION" == disable ]]; then
    systemctl --user disable --now localllm-helper-keepalive.timer 2>/dev/null || true
    echo "Helper keepalive timer disabled."
    exit 0
fi

if [[ -z "${HELPER_HOST:-}" ]]; then
    echo "No HELPER_HOST in $LOCAL_ENV_FILE yet: run ./localllm.sh setup-helper first."
    exit 0
fi

install_user_unit localllm-helper-keepalive.service \
    "$(sed "s|@HELPER_HOST@|$HELPER_HOST|g" "$REPO_DIR/services/localllm-helper-keepalive.service")"
