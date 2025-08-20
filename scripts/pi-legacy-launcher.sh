#!/usr/bin/env bash
set -euo pipefail

# Early Pi CLI wrapper for local LLM inference via llama.cpp provider

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Load environment from helper config if available
if [[ -f "$PROJECT_ROOT/config/helper.env" ]]; then
  source "$PROJECT_ROOT/config/helper.env"
fi

# Default to Qwen3-30B-A3B-Instruct-2507 when no model is specified
MODEL="${PI_MODEL:-Qwen3-30B-A3B-Instruct-2507}"
PORT="${PI_PORT:-18789}"

echo "[pi-launcher] Starting Pi with $MODEL on port $PORT"
echo "[pi-launcher] Model: $MODEL | Port: $PORT"

# Launch Pi with the llama.cpp provider pointing to the helper PC
exec node "$PROJECT_ROOT/node_modules/pi/bin/pi.js" \
  --provider llama.cpp \
  --model "$MODEL" \
  --port "$PORT" \
  "$@"
