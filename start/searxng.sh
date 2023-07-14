#!/usr/bin/env bash
# Starts SearXNG if it is down (it normally starts at boot), and repairs it after a network change:
# if the container has no internet while this machine does, the containers are recreated.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

log "SearXNG"

if searxng_sync_override; then
    echo "Recreating the containers to use it..."
    cd "$(dirname -- "${BASH_SOURCE[0]}")/../docker/searxng" || exit 1
    docker-compose up -d --force-recreate
elif curl -fsS -m 2 -o /dev/null "$SEARXNG_URL/" 2>/dev/null; then
    echo "Already running."
else
    cd "$(dirname -- "${BASH_SOURCE[0]}")/../docker/searxng" || exit 1
    docker-compose up -d
fi
wait_for "$SEARXNG_URL/" 30 || die "SearXNG did not become ready. Logs: docker compose logs core (in $SEARXNG_DIR)"
