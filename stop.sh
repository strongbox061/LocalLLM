#!/usr/bin/env bash
# Stops the LLaMA-7B local model server to free VRAM.
# Run ./localllm.sh stop searxng to keep search running.
exec "$(dirname -- "${BASH_SOURCE[0]}")/localllm.sh" stop
