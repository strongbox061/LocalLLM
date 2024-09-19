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
    for i in $(seq 1 60); do
        if curl -fsS -m 2 "$LLAMA_URL/health" >/dev/null 2>&1; then
            break
        fi
        sleep 5
    done
fi
echo "llama-server: $LLAMA_URL/v1"
