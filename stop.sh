#!/usr/bin/env bash
# Stops the LLaMA-7B local model server to free VRAM and about 19 GB of RAM.
# In split mode, stops helper PC's services first, then main PC's, llama-server last.
# Run ./localllm.sh stop searxng to keep search running.
exec "$(dirname -- "${BASH_SOURCE[0]}")/localllm.sh" stop
