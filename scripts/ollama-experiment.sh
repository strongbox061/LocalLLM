#!/usr/bin/env bash
# ollama-experiment.sh — Quick local serving comparison for GGML q4_0 models
# Usage: ./scripts/ollama-experiment.sh [llama-7b|vicuna-7b]
set -euo pipefail

MODEL="${1:-vicuna-7b}"
OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"
TIMEOUT=30

echo "=== Ollama Local Serving Comparison ==="
echo "Model : ${MODEL} (GGML q4_0)"
echo "Host  : ${OLLAMA_HOST}"
echo ""

# Pull the model if not already present
if ! curl -sf "${OLLAMA_HOST}/api/tags" | grep -q "\"${MODEL}\""; then
    echo "Pulling ${MODEL} (may take a few minutes)..."
    curl -sfX POST "${OLLAMA_HOST}/api/pull" -d "{\"name\":\"${MODEL}\"}" > /dev/null
fi

# Start serving in the background
echo "Starting ollama serve for ${MODEL}..."
ollama run "${MODEL}" "" &
SERVE_PID=$!
sleep 5

# Send a test prompt and measure response time
PROMPT="What is the purpose of this experiment?"
START_TIME=$(date +%s%N)

RESPONSE=$(curl -sf --max-time "${TIMEOUT}" \
    -X POST "${OLLAMA_HOST}/api/generate" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"${MODEL}\",\"prompt\":\"${PROMPT}\",\"stream\":false}")

END_TIME=$(date +%s%N)
ELAPSED=$(( (END_TIME - START_TIME) / 1000000 ))

echo ""
echo "Prompt : ${PROMPT}"
echo "Response: $(echo "${RESPONSE}" | grep -o '"response": *"[^"]*"' | head -c 200 | sed 's/"response": *"//;s/"$//')"
echo "Latency : ${ELAPSED}ms"
echo ""

# Clean up
kill "${SERVE_PID}" 2>/dev/null || true
echo "Done."
