#!/usr/bin/env bash
# gpu-limits.sh — set NVIDIA GPU clock and power limits for a user's local LLM session.
# Reads GPU_MAX_CLOCK_MHZ and
# laptop GPUs allow) and GPU_POWER_LIMIT_W (a power limit, which most laptop GPUs refuse: their limit is managed by the
# driver) from the user's ~/.config/localllm/local.env. Unset, the GPU's own defaults are put back.
#
#   gpu-limits.sh <user>             run as root by localllm-llm.service (ExecStartPre=+)
#   gpu-limits.sh <user> --dry-run   only prints what it would do (no root needed)
#

set -euo pipefail

# because this runs as root.
USER_NAME="${1:-}"
DRY_RUN=false
say() { echo "gpu-limits: $*"; }
run() {
  if "$DRY_RUN"; then
    say "$*"
  else
    eval "$@"
  fi
}

if [[ -z "$USER_NAME" ]]; then
  echo "Usage: $0 <user> [--dry-run]" >&2
  exit 1
fi

HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
ENV_FILE="$HOME_DIR/.config/localllm/local.env"

# A setting's value from local.env: the last uncommented NAME="value" line, digits only.
setting() {
  local name="$1"
  grep -E "^${name}=" "$ENV_FILE" 2>/dev/null \
    | tail -1 \
    | sed 's/.*=//; s/^["'"'"']//; s/["'"'"']$//' \
    | tr -cd '0-9' || true
}

MAX_CLOCK="$(setting GPU_MAX_CLOCK_MHZ)"
POWER_LIMIT="$(setting GPU_POWER_LIMIT_W)"

# Persistence mode keeps the driver loaded between processes, so the limits below aren't lost when the GPU goes idle.
if [[ -n "$MAX_CLOCK" ]]; then
  run nvidia-smi -i 0 -lgc "$MAX_CLOCK"
fi

if [[ -n "$POWER_LIMIT" ]]; then
  run nvidia-smi -i 0 -pl "$POWER_LIMIT"
fi

# Enable persistence mode so limits persist across GPU idle periods.
run nvidia-smi -pm ENABLED || say "warning: could not set persistence mode (may already be enabled)"

say "done — clock=${MAX_CLOCK:-default}MHz, power=${POWER_LIMIT:-default}W"
