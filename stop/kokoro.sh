#!/usr/bin/env bash
# Stops the voice router and the Kokoro text-to-speech server (removes the container; the image stays).
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

systemctl --user stop voice-router 2>/dev/null || true
kokoro_compose down 2>/dev/null || true
echo "Kokoro stopped."
