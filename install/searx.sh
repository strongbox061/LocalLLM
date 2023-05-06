#!/usr/bin/env bash
# Sets up SearXNG with Docker Compose. Its containers use "restart: always", so with docker.service
# enabled they start at boot. Files already there (for example restored by restore.sh) are kept. Images not here yet
# are pulled at the version recorded by the last backup (versions.env), if any.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

RAW="https://raw.githubusercontent.com/searxng/searxng/master/container"

mkdir -p "$SEARXNG_DIR/core-config"
cd "$SEARXNG_DIR"

# Keep SearXNG accessible only from this machine.
if [ ! -f "docker-compose.yml" ]; then
  curl -fsSL "${RAW}/docker-compose.yml" -o docker-compose.yml
fi

# Generate a secret key once.
if [ ! -f "core-config/settings.yml" ]; then
  SECRET_KEY=$(openssl rand -hex 32)
  cat > core-config/settings.yml <<EOF
use_default_settings: true
search:
  safe_search: 0
server:
  secret_key: "${SECRET_KEY}"
  limiter: false
  image_proxy: false
  publish_port: 8888
  bind_address: "127.0.0.1"
  method: "GET"
engines:
  - name: google
    engine: google
    shortcut: g
    disabled: false
  - name: duckduckgo
    engine: duckduckgo
    shortcut: ddg
    disabled: false
EOF
fi

# The images of the last backup (versions.env), when they aren't here yet.
if [ -f "versions.env" ]; then
  while IFS='=' read -r key value; do
    case "$key" in
      searxng|searxng*)
        docker pull "${value}" 2>/dev/null || true
        ;;
    esac
  done < versions.env
fi
