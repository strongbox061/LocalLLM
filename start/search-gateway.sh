#!/usr/bin/env bash
# Starts the search gateway Pi searches through, and waits until it has loaded the Open WebUI tool.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"
# In split mode it runs on the helper PC (its data is there): never start this PC's copy by mistake.
[[ "${WHERE_SEARCH_GATEWAY-main}" == main ]] \
    || die "The search gateway runs on the helper PC in split mode: ./localllm.sh start search-gateway (or switch single first)."

SEARCH_GATEWAY_URL="${SEARCH_GATEWAY_URL:-http://localhost:3195}"

log_info "Starting search gateway..."

# Start the service
systemctl --user start search-gateway

# Wait for it to be ready
for i in $(seq 1 30); do
    if curl -fsS -m 3 "$SEARCH_GATEWAY_URL/healthz" >/dev/null 2>&1; then
        log_ok "Search gateway is ready at $SEARCH_GATEWAY_URL"
        exit 0
    fi
    sleep 1
done

die "Search gateway failed to start within 30 seconds"
