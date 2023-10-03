#!/usr/bin/env bash
# Starts everything: ./localllm.sh start (README, "Starting and stopping"). In single mode that is llama-server,
# the main PC's services first, then the helper PC's. One service: ./localllm.sh start <name> (or start/<name>.sh).
exec "$(dirname -- "${BASH_SOURCE[0]}")/localllm.sh" start
