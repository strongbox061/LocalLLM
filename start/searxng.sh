#!/usr/bin/env bash
# Starts SearXNG if it is down (it normally starts at boot), and repairs it after a network change:
# if the container has no internet while this machine does, the containers are recreated.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

log "SearXNG"

if ! docker ps --format '{{.Names}}' | grep -q '^searxng$'; then
  log "SearXNG is not running, starting it..."
  cd "$(dirname -- "${BASH_SOURCE[0]}")/../docker/searxng" || exit 1
  docker-compose up -d
else
  log "SearXNG is already running"
fi
