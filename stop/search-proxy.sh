#!/usr/bin/env bash
# Stops the search gateway proxy service.
set -euo pipefail

PID_FILE="/tmp/search-gateway.pid"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    kill "$PID" 2>/dev/null || true
    rm -f "$PID_FILE"
    echo "Search gateway stopped (PID $PID)."
else
    echo "No search gateway found running."
fi
