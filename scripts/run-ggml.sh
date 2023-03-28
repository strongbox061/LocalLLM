#!/usr/bin/env bash
set -euo pipefail

# run-ggml.sh — Launch llama.cpp main binary with a GGML q4_0 model.
# Usage: ./scripts/run-ggml.sh [MODEL_PATH] [CONVERSATION_JSON]

LLAMA_CPP_DIR="${LLAMA_CPP_DIR:-$(dirname "$(dirname "$(realpath "$0")")")/llama.cpp}"
MODEL="${1:-$(cat "${MODEL_PATH_FILE:-.model_path}" 2>/dev/null || echo "")}"
CONV_FILE="${2:-$(cat "${CONV_FILE_PATH:-.conversation_json}" 2>/dev/null || echo "")}"

# Environment variable overrides for inference parameters
CTX_SIZE="${CTX_SIZE:-2048}"
N_PREDICT="${N_PREDICT:-256}"
TEMP="${TEMP:-0.7}"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  echo "Usage: $0 [MODEL_PATH] [CONVERSATION_JSON]"
  echo ""
  echo "Options:"
  echo "  MODEL_PATH        Path to GGML q4_0 model (or set MODEL_PATH env var)"
  echo "  CONVERSATION_JSON Optional conversation file"
  echo ""
  echo "Environment variables:"
  echo "  CTX_SIZE          Context size (default: ${CTX_SIZE})"
  echo "  N_PREDICT         Tokens to predict (default: ${N_PREDICT})"
  echo "  TEMP              Temperature (default: ${TEMP})"
  echo "  LLAMA_CPP_DIR     llama.cpp directory (default: auto-detect)"
  exit 0
fi

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
  --ctx-size "${CTX_SIZE}" \
  --n-predict "${N_PREDICT}" \
  --temp "${TEMP}" \
  -ngl 32 \
  "${CONV_ARGS[@]+"${CONV_ARGS[@]}"}"
