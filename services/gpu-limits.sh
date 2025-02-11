#!/usr/bin/env bash
# Caps the GPU before llama-server starts, so long model runs heat it less: GPU_MAX_CLOCK_MHZ (a clock cap, which
# laptop GPUs allow) and GPU_POWER_LIMIT_W (a power limit, which most laptop GPUs refuse: their limit is managed by the
# driver) from the user's ~/.config/localllm/local.env. Unset, the GPU's own defaults are put back.
#
#   services/gpu-limits.sh <user>             run as root by localllm-llm.service (ExecStartPre=+)
#   services/gpu-limits.sh <user> --dry-run   only prints what it would do (no root needed)
#
# It never stops llama-server from starting: any problem is printed and it exits 0. local.env is read, not sourced,
# because this runs as root.
set -uo pipefail

USER_NAME="${1:-}"
DRY_RUN=false
[[ "${2:-}" == "--dry-run" ]] && DRY_RUN=true

say() { echo "gpu-limits: $*"; }
run() {
    if $DRY_RUN; then say "would run: $*"; return 0; fi
    "$@" 2>&1 | sed 's/^/gpu-limits: /'
    return "${PIPESTATUS[0]}"
}

command -v nvidia-smi >/dev/null || { say "no nvidia-smi: nothing to do."; exit 0; }
[[ -n "$USER_NAME" ]] || { say "no user given: nothing to do."; exit 0; }
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
ENV_FILE="$HOME_DIR/.config/localllm/local.env"

# A setting's value from local.env: the last uncommented NAME="value" line, digits only.
setting() {
    [[ -f "$ENV_FILE" ]] || return 0
    sed -nE "s/^[[:space:]]*$1=[\"']?([0-9]+)[\"']?[[:space:]]*(#.*)?$/\1/p" "$ENV_FILE" | tail -1
}

MAX_CLOCK="$(setting GPU_MAX_CLOCK_MHZ)"
POWER_LIMIT="$(setting GPU_POWER_LIMIT_W)"

# Persistence mode keeps the driver loaded between processes, so the limits below aren't lost when the GPU goes idle.
[[ -n "$MAX_CLOCK$POWER_LIMIT" ]] && run nvidia-smi --persistence-mode=1 >/dev/null

if [[ -n "$MAX_CLOCK" ]]; then
    if (( MAX_CLOCK < 300 )); then
        say "GPU_MAX_CLOCK_MHZ=$MAX_CLOCK is too low (under 300 MHz): ignored."
    else
        if run nvidia-smi --lock-gpu-clocks="0,$MAX_CLOCK"; then say "GPU clock capped at $MAX_CLOCK MHz."
        else say "the GPU refused the clock cap; it runs at its default clocks."; fi
    fi
else
    if run nvidia-smi --reset-gpu-clocks; then say "GPU clocks: the GPU's defaults (GPU_MAX_CLOCK_MHZ unset)."
    else say "couldn't reset the GPU clocks."; fi
fi

if [[ -n "$POWER_LIMIT" ]]; then
    if run nvidia-smi --power-limit="$POWER_LIMIT"; then say "GPU power limit: $POWER_LIMIT W."
    else say "the GPU refused a power limit (most laptop GPUs do: the driver manages it); use GPU_MAX_CLOCK_MHZ."; fi
fi
exit 0
