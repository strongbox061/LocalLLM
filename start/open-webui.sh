#!/usr/bin/env bash
# Starts Open WebUI and waits until it answers.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

# In split mode it runs on the helper PC; when owui is not in LOCALLLM_APPS it stays off.
# Its data came from a backup made in split mode (restore.sh): its settings still name the helper's services. Set
# them for this PC, once (the Knowledge bases are indexed again with this PC's embedding model: a few minutes).

OWUI_PORT="${OWUI_PORT:-3000}"
OWUI_DATA_DIR="${OWUI_DATA_DIR:-${LOCAL_LLM_DATA_DIR:-$HOME/.local/share}/local-llm/open-webui}"

start_open_webui() {
  # If the container is already running, skip starting it.
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx open-webui; then
    log_info "Open WebUI is already running."
    return 0
  fi

  log_info "Starting Open WebUI on port ${OWUI_PORT}..."
  mkdir -p "${OWUI_DATA_DIR}"

  # Run the container in detached mode
  docker run -d \
    --name open-webui \
    --restart unless-stopped \
    -p "${OWUI_PORT}:8080" \
    -v "${OWUI_DATA_DIR}:/app/backend/data" \
    --gpus all \
    ghcr.io/open-webui/open-webui:main

  wait_for_service "Open WebUI" "http://localhost:${OWUI_PORT}" || {
    log_error "Open WebUI failed to start. Check logs with: docker logs open-webui"
    return 1
  }
}

stop_open_webui() {
  log_info "Stopping Open WebUI..."
  docker stop open-webui || true
}

case "${1:-start}" in
  start)  start_open_webui ;;
  stop)   stop_open_webui ;;
  *)
    echo "Usage: $0 {start|stop}"
    exit 1
    ;;
esac
