#!/usr/bin/env bash
# Start the Plannotator Inbox in the background and print its address.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

log "Plannotator Inbox"
PLANNOTATOR="$HOME/.local/bin/plannotator"
[[ -x "$PLANNOTATOR" ]] || die "plannotator is not installed. Run install/plannotator.sh first."

url="$("$PLANNOTATOR" inbox --background)" || die "The Plannotator Inbox did not start."
echo "Plannotator Inbox: $url"
