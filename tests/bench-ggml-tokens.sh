#!/usr/bin/env bash
set -euo pipefail

# bench-ggml-tokens.sh — Quick tokens/sec benchmark for GGML models via llama.cpp
# Usage: ./bench-ggml-tokens.sh <model.ggmlv3.q4_0.bin> [predict_tokens] [threads]

VERIFY=0
if [[ "${1:-}" == "--verify" ]]; then
  VERIFY=1
  shift
fi

MODEL="${1:?Usage: $0 [--verify] <model.ggmlv3.q4_0.bin> [predict_tokens] [threads]}"
TOKENS="${2:-512}"
THREADS="${3:-$(nproc 2>/dev/null || echo 4)}"

if [[ ! -f "$MODEL" ]]; then
  echo "Error: model not found: $MODEL" >&2
  exit 1
fi

echo "=== GGML tok/s Benchmark ==="
echo "Model : $(basename "$MODEL")"
echo "Tokens: $TOKENS"
echo "Threads: $THREADS"
echo ""

# Run llama.cpp predict mode and capture timing from stderr output
OUTPUT=$(./llama/main -m "$MODEL" -n "$TOKENS" -t "$THREADS" --log-disable 2>&1) || true

# Extract tokens/sec from llama.cpp's "toks/sec" or "per second" lines
TOKS_PER_SEC=$(echo "$OUTPUT" | grep -oP '\d+\.?\d*\s*toks?/sec' | tail -1 | grep -oP '[\d.]+') || true

if [[ -n "$TOKS_PER_SEC" ]]; then
  echo "Result: ${TOKS_PER_SEC} tok/s"
else
  # Fallback: measure elapsed time for known token count
  START=$(date +%s%N)
  ./llama/main -m "$MODEL" -n "$TOKENS" -t "$THREADS" --log-disable >/dev/null 2>&1 || true
  END=$(date +%s%N)
  ELAPSED_NS=$((END - START))
  ELAPSED_S=$(echo "scale=3; $ELAPSED_NS / 1000000000" | bc)
  TOKS_PER_SEC=$(echo "scale=2; $TOKENS / $ELAPSED_S" | bc)
  echo "Result: ${TOKS_PER_SEC} tok/s (${TOKENS} tokens in ${ELAPSED_S}s)"
fi

# Verification mode: check that tok/s is within expected range for GGML q4_0
if [[ "$VERIFY" -eq 1 ]]; then
  MIN_TOKS=$(echo "$TOKS_PER_SEC < 2" | bc -l)
  if [[ "$MIN_TOKS" -eq 1 ]]; then
    echo "FAIL: tok/s ($TOKS_PER_SEC) is below expected minimum (2) — model may be corrupted or CPU too slow" >&2
    exit 1
  fi
  echo "PASS: tok/s ($TOKS_PER_SEC) is within expected range for GGML q4_0"
fi
