#!/usr/bin/env bash
# Stops the voice router and the Kokoro text-to-speech server (removes the container; the image stays).
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

# Stop the voice router service
systemctl --user stop voice-router 2>/dev/null || true

# Stop Kokoro containers (with a short timeout to avoid hanging)
docker compose -f kokoro/docker-compose.yml down --timeout 5 2>/dev/null || true

echo "Kokoro stopped."
