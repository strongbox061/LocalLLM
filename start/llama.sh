#!/usr/bin/env bash
# Starts llama-server and waits until the model is loaded.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

log "llama-server"
if curl -fsS -m 2 -o /dev/null "$LLAMA_URL/health" 2>/dev/null; then
    echo "Already running."
else
    sudo systemctl start localllm-llm
    echo "Loading the model..."
    wait_for "$LLAMA_URL/health" 300 || die "llama-server did not become ready. Logs: journalctl -u localllm-llm -e"
fi
echo "llama-server: $LLAMA_URL/v1"
