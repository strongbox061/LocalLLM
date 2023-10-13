#!/usr/bin/env bash
# Stops Open WebUI.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

systemctl --user stop open-webui 2>/dev/null || true
echo "Open WebUI stopped."
