#!/usr/bin/env bash
# Installs the localllm-helper-keepalive user timer, which pings the helper PC over Tailscale every 60 seconds so
# the direct WireGuard path stays warm and SSH doesn't time out after idle periods.
#   install/helper-keepalive.sh            installs the units (requires HELPER_HOST in local.env)
#   install/helper-keepalive.sh --enable   installs and enables the timer
#   install/helper-keepalive.sh --disable  stops and disables the timer
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"
