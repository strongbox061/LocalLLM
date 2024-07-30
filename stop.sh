#!/usr/bin/env bash
# Stops everything but SearXNG: ./localllm.sh stop (README, "Starting and stopping"), which frees the VRAM and about
# 19 GB of RAM. In split mode, stops helper PC's services first, then main PC's, llama-server last.
# Run ./localllm.sh stop searxng to keep search running (or stop/searxng.sh).
exec "$(dirname -- "${BASH_SOURCE[0]}")/localllm.sh" stop
