#!/bin/sh
set -eu

SEARXNG_DIR="$HOME/LocalLLM/searxng"

if [ ! -d "$SEARXNG_DIR" ]; then
    echo "SearXNG directory does not exist: $SEARXNG_DIR"
    exit 0
fi

cd "$SEARXNG_DIR"

echo "Stopping SearXNG..."
docker compose down

echo "Removing SearXNG files..."
rm -rf "$SEARXNG_DIR"

echo "SearXNG installation removed."
