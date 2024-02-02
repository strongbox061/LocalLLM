#!/usr/bin/env bash
# Serve LLaMA-7B / Vicuna-7B GGML models via llama.cpp (main binary)
# Renamed from server.sh for clarity; relocated to scripts/ for organization.
# This PC only, in both modes; in split mode `tailscale serve` passes the helper PC's requests on (see below).

set -euo pipefail

# shellcheck source=../lib/model.sh
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/model.sh"

HOST="127.0.0.1"
PORT="8081"            # 8080 is SearXNG

# This PC's values come from local.env (see config/local.example.env):
# - LLAMA_VISION=off leaves the vision projector out: no image input, about 0.9 GB of RAM freed. To run it on the GPU
#   (faster image encoding), remove --no-mmproj-offload in lib/model.sh and raise LLAMA_N_CPU_MOE by about 2.
# - LLAMA_N_CPU_MOE (layers whose experts stay on the CPU) and LLAMA_CACHE_RAM_MIB (prompt cache) override the
#   profile's defaults.
# - LLAMA_THREADS: CPU threads, else the number of physical cores.

LLAMA_THREADS="${LLAMA_THREADS:-$(nproc)}"

MODEL_PATH="${MODEL_PATH:-$HOME/.cache/llm/models}"
MODEL_FILE=$(find_model_file "$MODEL_PATH")

if [[ -z "${MODEL_FILE:-}" ]]; then
  echo "ERROR: No GGML model found in $MODEL_PATH" >&2
  echo "Run setup-llama.sh to download one, or set MODEL_FILE explicitly." >&2
  exit 1
fi

echo "Serving $(basename "$MODEL_FILE") on ${HOST}:${PORT}"
echo "Threads: $LLAMA_THREADS"

exec ./bin/llama/main \
  -m "$MODEL_FILE" \
  -t "$LLAMA_THREADS" \
  --host "$HOST" \
  --port "$PORT" \
  --ctx-size 2048 \
  --threads-batch "$LLAMA_THREADS" \
  --log-disable
