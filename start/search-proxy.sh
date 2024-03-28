#!/usr/bin/env bash
# Starts the search gateway Pi searches through, and waits until it has loaded the Open WebUI tool.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"
# In split mode it runs on the helper PC (its data is there): never start this PC's copy by mistake.
[[ "${WHERE_SEARCH_GATEWAY-main}" == main ]] \
    || die "The search gateway runs on the helper PC in split mode: ./localllm.sh start search-gateway (or switch single first)."

SEARCH_PORT="${SEARCH_PORT:-3195}"

log_info "Starting search gateway on port ${SEARCH_PORT}..."

# Start the Python server in the background
python3 -m http.server "${SEARCH_PORT}" &
SERVER_PID=$!

log_info "Search gateway started (PID ${SERVER_PID})"

# Wait for it to be ready
for i in $(seq 1 30); do
    if curl -sf "http://localhost:${SEARCH_PORT}/" >/dev/null 2>&1; then
        log_ok "Search gateway is ready on port ${SEARCH_PORT}"
        exit 0
    fi
    sleep 1
done

die "Search gateway failed to start within 30 seconds"
