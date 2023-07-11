#!/usr/bin/env bash
# Starts the LocalLLM system monitor web page (CPU, GPU, RAM on main & helper PCs).
# Ctrl+C stops it.
#
#   ./monitor.sh [--port 8095] [--interval 5] [--once]
set -euo pipefail

PORT=8095
INTERVAL=5
ONCE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)    PORT="$2"; shift 2 ;;
        --interval) INTERVAL="$2"; shift 2 ;;
        --once)    ONCE=true; shift ;;
        *)         echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# shellcheck source=lib/common.sh
source "$(dirname -- "${BASH_SOURCE[0]}")/lib/common.sh"

command -v python3 >/dev/null || die "python3 is required to run the system monitor."

exec python3 "$REPO_DIR/tools/monitor/monitor.py" --port "$PORT" --interval "$INTERVAL" $([ "$ONCE" = true ] && echo --once)