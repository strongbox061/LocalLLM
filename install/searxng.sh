#!/usr/bin/env bash
# Sets up SearXNG with Docker Compose. Its containers use "restart: always", so with docker.service
# enabled they start at boot. Files already there (for example restored by restore.sh) are kept. Images not here yet
# are pulled at the version recorded by the last backup (versions.env), if any.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

RAW="https://raw.githubusercontent.com/searxng/searxng/master/container"

mkdir -p "$SEARXNG_DIR/core-config"
cd "$SEARXNG_DIR"

[[ -f docker-compose.yml ]] || curl -fsSL -o docker-compose.yml "$RAW/docker-compose.yml"
grep -q 'restart: always' docker-compose.yml \
    || warn "docker-compose.yml has no 'restart: always': SearXNG would not start at boot."

if [[ ! -f .env ]]; then
    curl -fsSL -o .env "$RAW/.env.example"
fi

# Keep SearXNG accessible only from this machine.
if grep -q '^SEARXNG_HOST=' .env; then
    grep -qx 'SEARXNG_HOST=127.0.0.1' .env || sed -i 's/^SEARXNG_HOST=.*/SEARXNG_HOST=127.0.0.1/' .env
else
    printf '\nSEARXNG_HOST=127.0.0.1\n' >> .env
fi

# Generate a secret key once.
if [[ ! -f core-config/settings.yml ]]; then
    SECRET_KEY="$(openssl rand -hex 32)"

    cat > core-config/settings.yml <<EOF
use_default_settings: true

server:
  secret_key: "$SECRET_KEY"

search:
  formats:
    - html
    - json
EOF
fi

searxng_sync_override || true
# The images of the last backup (versions.env), when they aren't here yet.
pin_image "${SEARXNG_IMAGE:-}" docker-compose.yml
pin_image "${VALKEY_IMAGE:-}" docker-compose.yml
docker_cmd compose up -d

echo "Waiting for SearXNG..."

for _ in $(seq 1 30); do
    if curl -fsS "$SEARXNG_URL/" >/dev/null 2>&1; then
        break
    fi

    if ! docker_cmd compose ps --status running --services | grep -qx core; then
        echo "ERROR: SearXNG container stopped."
        docker_cmd compose logs --tail=100 core
        exit 1
    fi

    sleep 1
done

if ! curl -fsS "$SEARXNG_URL/" >/dev/null 2>&1; then
    echo "ERROR: SearXNG did not become ready."
    docker_cmd compose logs --tail=100 core
    exit 1
fi

docker_cmd compose ps

echo
echo "SearXNG search test:"

curl -fsS "$SEARXNG_URL/search?q=llama.cpp&format=json" | head -c 2000

echo
echo
echo "SearXNG: $SEARXNG_URL"
