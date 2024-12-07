#!/usr/bin/env bash
# Starts the LocalLLM system monitor web page (CPU, GPU, RAM on main & helper PCs,
# plus single/split and pi/pi,owui mode). Ctrl+C stops it.
#
#   ./tools/monitor/monitor.sh [--port 8095] [--interval 5] [--once]
set -euo pipefail

# shellcheck source=../../lib/common.sh
source "$(dirname -- "${BASH_SOURCE[0]}")/../../lib/common.sh"

command -v python3 >/dev/null || die "python3 is required to run the system monitor."

exec python3 "$REPO_DIR/tools/monitor/monitor.py" "$@"
