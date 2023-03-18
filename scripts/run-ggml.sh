#!/usr/bin/env bash
set -euo pipefail

# run-ggml.sh — Launch llama.cpp main binary with a GGML q4_0 model.
# Usage: ./scripts/run-ggml.sh [MODEL_PATH] [CONVERSATION_JSON]

LLAMA_CPP_DIR="${LLAMA_CPP_DIR:-$(dirname "$(dirname "$(realpath "$0")")")/llama.cpp}"
MODEL="${1:-$(cat "${MODEL_PATH_FILE:-.model_path}" 2>/dev/null || echo "")}"
CONV_FILE="${2:-$(cat "${CONV_FILE_PATH:-.conversation_json}" 2>/dev/null || echo "")}"

if [[ -z "${MODEL}" ]]; then
  echo "Error: no model specified. Set MODEL_PATH or pass as first argument." >&2
  exit 1
fi

if [[ ! -f "${MODEL}" ]]; then
  echo "Error: model file not found: ${MODEL}" >&2
  exit 1
fi

CONV_ARGS=()
if [[ -n "${CONV_FILE}" && -f "${CONV_FILE}" ]]; then
  CONV_ARGS+=("-f" "${CONV_FILE}")
fi

exec "${LLAMA_CPP_DIR}/main" \
  --model "${MODEL}" \
  --ctx-size 2048 \
  --n-predict 256 \
  --temp 0.7 \
  -ngl 32 \
  "${CONV_ARGS[@]+"${CONV_ARGS[@]}"}"
