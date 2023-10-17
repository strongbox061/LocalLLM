#!/usr/bin/env bash
set -euo pipefail

# Open WebUI Docker container launcher
# Wraps docker run for open-webui/open-webui image

CONTAINER_NAME="open-webui"
IMAGE_NAME="ghcr.io/open-webui/open-webui:main"
HOST_PORT=8080
DATA_DIR="${HOME}/.local/share/open-webui"

# Check if container is already running
if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "Open WebUI is already running (container: ${CONTAINER_NAME})"
    exit 0
fi

# Ensure data directory exists
mkdir -p "${DATA_DIR}"

echo "Starting Open WebUI container..."
docker run -d \
    --name "${CONTAINER_NAME}" \
    --restart unless-stopped \
    -p "${HOST_PORT}:8080" \
    -v "${DATA_DIR}:/app/backend/data" \
    -e OLLAMA_BASE_URL="http://localhost:11434" \
    "${IMAGE_NAME}"

echo "Open WebUI starting on http://localhost:${HOST_PORT}"
echo "Container: ${CONTAINER_NAME}"
