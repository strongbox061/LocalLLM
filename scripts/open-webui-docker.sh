#!/usr/bin/env bash
set -euo pipefail

# Open WebUI Docker container launcher
# Wraps docker run for open-webui/open-webui image

CONTAINER_NAME="open-webui"
IMAGE_NAME="ghcr.io/open-webui/open-webui:main"
HOST_PORT=8080
DATA_DIR="${HOME}/.local/share/open-webui"

usage() {
    echo "Usage: $0 [start|stop|remove]"
    echo "  start   - Start the container (default)"
    echo "  stop    - Stop the running container"
    echo "  remove  - Stop and remove the container"
    exit 1
}

ACTION="${1:-start}"

case "${ACTION}" in
    stop)
        if ! docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
            echo "Open WebUI container '${CONTAINER_NAME}' not found"
            exit 1
        fi
        if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
            echo "Stopping Open WebUI container..."
            docker stop "${CONTAINER_NAME}"
            echo "Container stopped"
        else
            echo "Container is not running"
        fi
        exit 0
        ;;
    remove)
        if ! docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
            echo "Open WebUI container '${CONTAINER_NAME}' not found"
            exit 1
        fi
        if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
            echo "Stopping Open WebUI container..."
            docker stop "${CONTAINER_NAME}"
        fi
        echo "Removing Open WebUI container..."
        docker rm "${CONTAINER_NAME}"
        echo "Container removed"
        exit 0
        ;;
    start|"")
        # Check if container is already running or exists
        if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
            echo "Open WebUI container '${CONTAINER_NAME}' already exists"
            if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
                echo "Container is already running"
                exit 0
            fi
            echo "Starting stopped container..."
            docker start "${CONTAINER_NAME}"
            echo "Open WebUI starting on http://localhost:${HOST_PORT}"
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
        ;;
    *)
        usage
        ;;
esac
