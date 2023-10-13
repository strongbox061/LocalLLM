#!/usr/bin/env bash
# Stops Open WebUI.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

systemctl --user stop open-webui 2>/dev/null || true
if pgrep -f "open-webui" > /dev/null 2>&1; then
    warn "Open WebUI may still be running (started outside systemd)."
else
    echo "Open WebUI stopped."
fi
