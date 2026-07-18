#!/usr/bin/env bash
# Writes the llama.cpp provider into Pi's models.json: sized from the active model profile (lib/model.sh), served under
# the alias "local". A models.json put back by restore.sh keeps everything else; only the llamacpp provider is
# rewritten. In split mode (LOCALLLM_MODE in local.env) llama-server asks for an API key, since the helper PC reaches
# it over Tailscale: Pi gets the key from ~/.config/localllm/llama-api-key (then models.json is made yours only).
# Run by install/pi.sh and by ./localllm.sh switch.

set -euo pipefail

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
if os.path.exists(path):
    with open(path, "r") as f:
        config = json.load(f)
else:
    config = {"providers": []}

# Build the llamacpp provider entry
provider = {
    "name": "llamacpp",
    "model_id": os.environ["MODEL_ID"],
    "context_length": int(os.environ["CONTEXT"]),
    "max_tokens": int(os.environ["MAX_TOKENS"])
}

if os.environ.get("API_KEY", ""):
    provider["api_key"] = os.environ["API_KEY"]

# Replace or add the llamacpp provider
providers = config.get("providers", [])
found = False
for i, p in enumerate(providers):
    if p.get("name") == "llamacpp":
        providers[i] = provider
        found = True
        break
if not found:
    providers.append(provider)

config["providers"] = providers

# Write back
with open(path, "w") as f:
    json.dump(config, f, indent=2)
    f.write("\n")

print(f"Updated {path}: llamacpp provider for {os.environ['MODEL_NAME']} (ctx={os.environ['CONTEXT']}, max_tokens={os.environ['MAX_TOKENS']})")
EOF_PY
