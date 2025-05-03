#!/usr/bin/env bash
# Stops the SearXNG search engine containers.
set -euo pipefail

cd "$(dirname -- "${BASH_SOURCE[0]}")/.."

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

searxng_compose down
echo "SearXNG stopped. Start it again with start/searxng.sh"