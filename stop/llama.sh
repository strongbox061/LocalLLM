#!/usr/bin/env bash
# Stops llama-server, which frees the VRAM.
set -euo pipefail

sudo systemctl stop localllm-llm
