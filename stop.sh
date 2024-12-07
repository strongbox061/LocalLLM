#!/usr/bin/env bash
# Stops everything but SearXNG: ./localllm.sh stop (README, "Starting and stopping"), which frees the VRAM and about
# 19 GB of RAM. In split mode the helper PC's services first, then the main PC's, llama-server last. SearXNG keeps
# running: ./localllm.sh stop searxng (or stop/searxng.sh).
exec "$(dirname -- "${BASH_SOURCE[0]}")/localllm.sh" stop
