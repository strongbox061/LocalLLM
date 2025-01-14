#!/usr/bin/env bash
# Stop the Kokoro TTS server and voice router containers.
set -euo pipefail

cd "$(dirname -- "${BASH_SOURCE[0]}")/.."

# Stop the voice router service
systemctl --user stop voice-router 2>/dev/null || true

# Stop Kokoro containers
docker compose -f kokoro/docker-compose.yml down 2>/dev/null || true

echo "Kokoro stopped."
