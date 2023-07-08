#!/usr/bin/env bash
# Set laptop fan to performance mode for local LLM inference
set -euo pipefail

ENV_FILE="$HOME/.config/localllm/local.env"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "Error: $ENV_FILE not found" >&2
    exit 1
fi

source "$ENV_FILE"

PERF_CMD="${FAN_PROFILE_PERFORMANCE:-}"
if [[ -z "$PERF_CMD" ]]; then
    echo "FAN_PROFILE_PERFORMANCE not set in $ENV_FILE" >&2
    exit 1
fi

eval $PERF_CMD
