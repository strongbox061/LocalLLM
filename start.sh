#!/usr/bin/env bash
# Start local LLM services — main PC first, then helper PC if available.
# One service: ./localllm.sh start <name> (or start/<name>.sh).
exec "$(dirname -- "${BASH_SOURCE[0]}")/localllm.sh" start
