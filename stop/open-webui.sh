#!/usr/bin/env bash
# Stops Open WebUI.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

systemctl --user stop open-webui 2>/dev/null || true
if webui_running; then
    warn "Open WebUI is still running: it was started by hand with services/open-webui.sh, stop it in its terminal."
else
    echo "Open WebUI stopped."
fi
