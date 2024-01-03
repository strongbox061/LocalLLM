#!/usr/bin/env bash
# Switches the laptop's fan profile to balanced: runs FAN_PROFILE_BALANCED from ~/.config/localllm/local.env, a command of your
# laptop vendor's fan tool (see config/local.example.env; the tool's package goes in EXTRA_PACKAGES).
set -euo pipefail
LOCAL_ENV_FILE="$HOME/.config/localllm/local.env"
# shellcheck source=/dev/null
[[ -f "$LOCAL_ENV_FILE" ]] && source "$LOCAL_ENV_FILE"
if [[ -z "${FAN_PROFILE_BALANCED:-}" ]]; then
    echo "FAN_PROFILE_BALANCED isn't set in $LOCAL_ENV_FILE: set it to your fan tool's command (see config/local.example.env)." >&2
    exit 1
fi
bash -c "$FAN_PROFILE_BALANCED"
