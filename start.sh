#!/usr/bin/env bash
# Starts everything: ./localllm.sh start (README, "Starting and stopping"). In single mode that is llama-server,
# the main PC's services first, then the helper PC's. One service: ./localllm.sh start <name> (or start/<name>.sh).
SCRIPT_DIR="$(dirname -- "${BASH_SOURCE[0]}")"
[ -x "$SCRIPT_DIR/localllm.sh" ] || { echo "error: $SCRIPT_DIR/localllm.sh not found or not executable" >&2; exit 1; }
exec "$SCRIPT_DIR/localllm.sh" start
