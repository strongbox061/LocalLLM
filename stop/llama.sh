#!/usr/bin/env bash
# Stops llama-server, which frees the VRAM.
set -euo pipefail

sudo systemctl stop localllm-llm

# Verify the process has actually stopped
sleep 2
if pgrep -f llama-server > /dev/null; then
    echo "Warning: llama-server may still be running" >&2
fi
