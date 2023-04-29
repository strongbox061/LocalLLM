#!/usr/bin/env bash
# Stops the SearXNG search engine containers.
# Run this when you want to tear down SearXNG (e.g. before network changes).
set -euo pipefail

cd "$(dirname -- "${BASH_SOURCE[0]}")/.."

docker compose -f docker-compose.searxng.yml down
echo "SearXNG stopped."