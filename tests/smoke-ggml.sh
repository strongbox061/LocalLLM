#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

source "$ROOT_DIR/common.sh"

echo "=== GGML Smoke Test ==="
echo "Model: $MODEL_FILE"
echo "Date:  $(date -u '+%Y-%m-%d %H:%M:%S UTC')"

# Verify model file exists
if [[ ! -f "$MODEL_FILE" ]]; then
  echo "ERROR: Model not found at $MODEL_FILE"
  echo "Run ./build-llama.sh first to compile llama.cpp, then download a GGML model."
  exit 1
fi

# Quick prompt test with llama.cpp main binary
PROMPT="Q: What is the capital of France? A:"
OUTPUT=$("$LLAMA_MAIN" \
  --model "$MODEL_FILE" \
  --prompt "$PROMPT" \
  --n-predict 16 \
  --temp 0.7 \
  -ngl 0 \
  2>/dev/null) || {
    echo "ERROR: llama.cpp main failed to run"
    exit 1
}

echo "Prompt: $PROMPT"
echo "Output: $OUTPUT"

# Basic sanity check — output should contain a non-empty response
if [[ -z "$OUTPUT" ]]; then
  echo "FAIL: Empty output from model"
  exit 1
fi

# Second test — instruction-style prompt
PROMPT2="In one sentence, explain what a neural network is."
OUTPUT2=$("$LLAMA_MAIN" \
  --model "$MODEL_FILE" \
  --prompt "$PROMPT2" \
  --n-predict 32 \
  --temp 0.5 \
  -ngl 0 \
  2>/dev/null) || {
    echo "ERROR: llama.cpp main failed on instruction prompt"
    exit 1
}

echo "Prompt: $PROMPT2"
echo "Output: $OUTPUT2"

if [[ -z "$OUTPUT2" ]]; then
  echo "FAIL: Empty output from model on instruction prompt"
  exit 1
fi

echo "PASS: Smoke test completed successfully"
