#!/usr/bin/env bash
# Stops the search gateway Pi searches through.
set -euo pipefail

systemctl --user stop search-gateway 2>/dev/null || true
echo "Search gateway stopped."
