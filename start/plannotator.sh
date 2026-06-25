#!/usr/bin/env bash
# Starts the Plannotator Inbox (where Pi leaves you messages) in the background, without opening a browser tab, and
# prints its address. Open the window with `plannotator inbox --open` (or /plannotator-inbox in Pi).
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

log "Plannotator Inbox"
PLANNOTATOR="$(command -v plannotator || echo "$HOME/.local/bin/plannotator")"
[[ -x "$PLANNOTATOR" ]] || die "plannotator is not installed. Run install/plannotator.sh first."

url="$("$PLANNOTATOR" inbox --background)" || die "The Plannotator Inbox did not start."
echo "Plannotator Inbox: $url"
