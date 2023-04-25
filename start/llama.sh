#!/usr/bin/env bash
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

# Start the llama.cpp local model server if not already running
if ! curl -fsS -m 2 -o /dev/null "http://localhost:8080/health" 2>/dev/null; then
    echo "Starting LLaMA server..."
    sudo systemctl start localllm-llm
    # Wait for the model to load into memory
    sleep 10
    echo "Model loading, please wait..."
fi
