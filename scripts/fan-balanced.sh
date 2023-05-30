#!/usr/bin/env bash
# Switches the laptop's fan profile to balanced mode
set -euo pipefail

LOCAL_ENV_FILE="$HOME/.config/localllm/local.env"
[[ -f "$LOCAL_ENV_FILE" ]] && source "$LOCAL_ENV_FILE"

if [[ -z "${FAN_PROFILE_BALANCED:-}" ]]; then
    echo "Error: FAN_PROFILE_BALANCED not set in $LOCAL_ENV_FILE" >&2
    exit 1
fi

eval "$FAN_PROFILE_BALANCED"
