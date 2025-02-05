#!/usr/bin/env bash
# Starts the search gateway Pi searches through, and waits until it has loaded the Open WebUI tool.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"
# In split mode it runs on the helper PC (its data is there): never start this PC's copy by mistake.
[[ "${WHERE_SEARCH_GATEWAY-main}" == main ]] \
    || die "The search gateway runs on the helper PC in split mode: ./localllm.sh start search-gateway (or switch single first)."

log "Search gateway"
if curl -fsS -m 3 -o /dev/null "$SEARCH_GATEWAY_URL/healthz" 2>/dev/null; then
    echo "Already running."
else
    systemctl --user start search-gateway
    echo "Starting..."
    if ! wait_for "$SEARCH_GATEWAY_URL/healthz" 30; then
        # /healthz answers 500 with the reason when the tool cannot be loaded.
        reason="$(curl -sS -m 3 "$SEARCH_GATEWAY_URL/healthz" 2>/dev/null || true)"
        [[ -z "$reason" ]] || echo "Server says: $reason" >&2
        die "The search gateway did not become ready. Logs: journalctl --user -u search-gateway -e"
    fi
fi
echo "Search gateway: $SEARCH_GATEWAY_URL (search usage: $SEARCH_GATEWAY_URL/dashboard)"
