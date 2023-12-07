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

if searxng_online; then
    echo "Internet from the container: OK"
elif ! host_online; then
    warn "This machine is offline, so SearXNG cannot search either. Connect to a network, then run this again."
else
    echo "The container has no internet while this machine does (stale network settings). Recreating..."
    cd "$(dirname -- "${BASH_SOURCE[0]}")/../docker/searxng" || exit 1
    docker-compose up -d --force-recreate
    wait_for "$SEARXNG_URL/" 30 || die "SearXNG did not become ready. Logs: docker compose logs core (in $SEARXNG_DIR)"
    if searxng_online; then
        echo "Internet from the container: OK"
    else
        warn "SearXNG still has no internet. Check in $SEARXNG_DIR:"
        warn "  docker compose exec core cat /etc/resolv.conf"
        warn "  docker compose exec core wget -q --spider -T 5 http://1.1.1.1 && echo 'IP works: DNS problem'"
        warn "A firewall or VPN started after Docker can also cause this: sudo systemctl restart docker"
        exit 1
    fi
fi
echo "SearXNG: $SEARXNG_URL"
