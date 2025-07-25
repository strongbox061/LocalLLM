#!/usr/bin/env bash
# Stops SearXNG and removes its containers (its data volumes are kept). Not called by stop.sh.
# Removing them means the next start builds fresh containers with the current network settings.
# After this it does NOT come back at boot by itself: run start/searxng.sh (or ./start.sh).
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

searxng_compose down
echo "SearXNG stopped. Start it again with start/searxng.sh"
