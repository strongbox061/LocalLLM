#!/usr/bin/env bash
# Writes the llama.cpp provider into Pi's models.json: sized from the active model profile (lib/model.sh), served under
# the alias "local". A models.json put back by restore.sh keeps everything else; only the llamacpp provider is
# rewritten. In split mode (LOCALLLM_MODE in local.env) llama-server asks for an API key, since the helper PC reaches
# it over Tailscale: Pi gets the key from ~/.config/localllm/llama-api-key (then models.json is made yours only).
# Run by install/pi.sh and by ./localllm.sh switch.

set -euo pipefail

mkdir -p "$PI_AGENT_DIR"
MODELS_JSON="$PI_AGENT_DIR/models.json"
MODEL_ALIAS="local"
MODEL_NAME="${MODEL_NAME:-Qwen3-30B-A3B-Instruct-2507}"
CTX_PER_SLOT=128
MAX_TOKENS=4096

# Load model profile if available
if [[ -f "${PI_AGENT_DIR}/lib/model.sh" ]]; then
    source "${PI_AGENT_DIR}/lib/model.sh"
fi

CONTEXT=$((CTX_PER_SLOT - MAX_TOKENS))

# Read API key for split mode
API_KEY=""
if [[ -n "${LOCALLLM_MODE:-}" ]] && [[ -f "$HOME/.config/localllm/llama-api-key" ]]; then
    API_KEY="$(cat "$HOME/.config/localllm/llama-api-key")"
fi

# Generate models.json using embedded Python
# Generate models.json using embedded Python (heredoc to avoid quoting issues)
MODEL_ID="$MODEL_ALIAS" MODEL_NAME="$MODEL_NAME" CONTEXT="$CONTEXT" MAX_TOKENS="$MAX_TOKENS" \
    API_KEY="$API_KEY" python3 - "$MODELS_JSON" <<'EOF_PY'
import json, os, sys

path = sys.argv[1]
try:
    with open(path) as f:
        data = json.load(f)
except FileNotFoundError:
    data = {"$schema": "https://pi.dev/schemas/models.schema.json"}
provider = data.setdefault("providers", {}).setdefault("llamacpp", {})
key = os.environ.get("API_KEY", "")
if key:
    provider["apiKey"] = key
provider.update({"baseUrl": os.environ["LLAMA_URL"] + "/v1"})
models = [{"id": os.environ["MODEL_ID"], "name": os.environ["MODEL_NAME"],
           "contextWindow": int(os.environ["CONTEXT"]), "maxTokens": int(os.environ["MAX_TOKENS"])}]
provider["models"] = models
with open(path, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
print(f"Wrote {path} (model {os.environ['MODEL_ID']}: {os.environ['MODEL_NAME']})")
EOF_PY
