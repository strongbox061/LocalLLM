#!/usr/bin/env bash
# Starts llama-server with the active model profile (models/<name>.env, chosen by MODEL_PROFILE in
# ~/.config/localllm/local.env) for Pi + Open WebUI, served under the alias "local".
# SLOTS slots of CTX_PER_SLOT tokens each, so Pi and Open WebUI keep separate caches.

# shellcheck source=../lib/model.sh
source "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")/../lib/model.sh"
load_model_profile || exit 1
write_model_json

LLAMA_SERVER="$HOME/LocalLLM/llama.cpp/build/bin/llama-server"

# This PC only, in both modes; in split mode `tailscale serve` passes the helper PC's requests on (see below).
HOST="127.0.0.1"
PORT="8081"            # 8080 is SearXNG

# This PC's values come from local.env (see config/local.example.env):
# - LLAMA_VISION=off leaves the vision projector out: no image input, about 0.9 GB of RAM freed. To run it on the GPU
#   (faster image encoding), remove --no-mmproj-offload in lib/model.sh and raise LLAMA_N_CPU_MOE by about 2.
# - LLAMA_N_CPU_MOE (layers whose experts stay on the CPU) and LLAMA_CACHE_RAM_MIB (prompt cache) override the
#   profile's defaults.
# - LLAMA_THREADS: CPU threads, else the number of physical cores.
VISION="${LLAMA_VISION:-on}"
THREADS="${LLAMA_THREADS:-$(lscpu -p=SOCKET,CORE | grep -v '^#' | sort -u | wc -l)}"

llm_server_args
# Split mode (LOCALLLM_MODE in local.env): the helper PC reaches llama-server over Tailscale (./localllm.sh forwards
# the port with `tailscale serve`; llama-server itself stays on 127.0.0.1), so every request must carry the API key.
if [[ "${LOCALLLM_MODE:-single}" == split ]]; then
    KEY_FILE="$HOME/.config/localllm/llama-api-key"
    [[ -s "$KEY_FILE" ]] || { echo "ERROR: split mode needs $KEY_FILE (./localllm.sh switch split makes it)" >&2; exit 1; }
    LLM_ARGS+=(--api-key-file "$KEY_FILE")
fi
exec "$LLAMA_SERVER" "${LLM_ARGS[@]}"
