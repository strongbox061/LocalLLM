#!/usr/bin/env bash
# Stops the search gateway Pi searches through.
set -euo pipefail

# Attempt systemctl first, fall back to PID file for non-systemd environments.
if command -v systemctl &>/dev/null; then
    systemctl --user stop search-gateway 2>/dev/null || true
else
    PID_FILE="/tmp/search-gateway.pid"
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        kill "$PID" 2>/dev/null || true
        rm -f "$PID_FILE"
    fi
fi
echo "Search gateway stopped."
