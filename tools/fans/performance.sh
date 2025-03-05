#!/usr/bin/env bash
# Switches the laptop's fan profile to performance: runs FAN_PROFILE_PERFORMANCE from ~/.config/localllm/local.env
set -euo pipefail

ENV_FILE="$HOME/.config/localllm/local.env"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "Error: $ENV_FILE not found" >&2
    exit 1
fi

source "$ENV_FILE"

PERF_CMD="${FAN_PROFILE_PERFORMANCE:-}"
if [[ -z "$PERF_CMD" ]]; then
    echo "FAN_PROFILE_PERFORMANCE isn't set in $ENV_FILE: configure it to your fan tool's command (see config/local.example.env)." >&2
    exit 1
fi

bash -c "$FAN_PROFILE_PERFORMANCE"
