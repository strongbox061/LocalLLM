#!/usr/bin/env bash
set -euo pipefail
# bench-qwen2-vram.sh — VRAM & kv-cache benchmark for Qwen2-7B-Instruct
# Usage: ./tests/bench-qwen2-vram.sh [n-glayers] [ctx-len]

MODEL="${MODEL:-models/qwen2-7b-instruct-q4_k_m.gguf}"
N_GLAYERS="${1:-32}"
CTX_LEN="${2:-4096}"
N_PROMPT=512
N_PREDICT=128
BATCH=512

echo "=== Qwen2-7B-Instruct VRAM / kv-cache benchmark ==="
echo "Model : $(basename "$MODEL")"
echo "GPU layers : $N_GLAYERS"
echo "Context    : $CTX_LEN"
echo "Prompt len : $N_PROMPT  |  Tokens to predict : $N_PREDICT"
echo ""

# Measure VRAM usage via nvidia-smi snapshot
before_vram=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i 0 2>/dev/null \
               || echo "0")

# Run llama-bench (ships with llama.cpp)
llama-bench \
  -m "$MODEL" \
  -n "$N_PREDICT" \
  -ngl "$N_GLAYERS" \
  -t "$(nproc)" \
  -b "$BATCH" \
  -c "${CTX_LEN},${N_PROMPT}" \
  --no-mmap 2>&1

after_vram=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i 0 2>/dev/null \
             || echo "0")
vram_delta=$(( after_vram - before_vram ))

echo ""
echo "--- Summary ---"
echo "VRAM delta : ${vram_delta} MB"
echo "Context    : ${CTX_LEN} tokens (${N_PROMPT} prompt / ${N_PREDICT} predict)"
echo "GPU layers : $N_GLAYERS / 32"

# Sanity checks
if [ "$vram_delta" -lt 0 ] 2>/dev/null; then
  echo "WARNING: negative VRAM delta — nvidia-smi may not be available"
fi

if [ "$N_GLAYERS" -gt 32 ]; then
  echo "WARNING: $N_GLAYERS layers exceeds model size (32); some may fall back to CPU"
fi

echo ""
echo "Benchmark complete."
