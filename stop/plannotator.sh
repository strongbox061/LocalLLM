#!/usr/bin/env bash
# Stops the Plannotator Inbox. Its threads are kept; Pi starts it again in the background the next time it sends.
set -euo pipefail

PLANNOTATOR="$HOME/.local/bin/plannotator"
[[ -x "$PLANNOTATOR" ]] && "$PLANNOTATOR" inbox stop >/dev/null 2>&1 || true
echo "Plannotator Inbox stopped."
