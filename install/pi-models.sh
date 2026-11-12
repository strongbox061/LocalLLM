#!/usr/bin/env bash
# Writes the llama.cpp provider into Pi's models.json: sized from the active model profile (lib/model.sh), served under
# the alias "local". A models.json put back by restore.sh keeps everything else; only the llamacpp provider is
# rewritten. In split mode (LOCALLLM_MODE in local.env) llama-server asks for an API key, since the helper PC reaches
# it over Tailscale: Pi gets the key from ~/.config/localllm/llama-api-key (then models.json is made yours only).
# Run by install/pi.sh and by ./localllm.sh switch.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"
source "$REPO_DIR/lib/model.sh"
load_model_profile || exit 1
MODELS_JSON="$PI_AGENT_DIR/models.json"
mkdir -p "$PI_AGENT_DIR"
MODEL_ID="$MODEL_ALIAS" MODEL_NAME="$MODEL_NAME" CONTEXT=$((CTX_PER_SLOT - MAX_TOKENS)) MAX_TOKENS="$MAX_TOKENS" \
    LLAMA_URL="$LLAMA_URL" API_KEY="$(llama_api_key_for_clients)" python3 - "$MODELS_JSON" <<'EOF_PY'
import json, os, sys
path = sys.argv[1]
try:
    with open(path) as f:
        data = json.load(f)
except FileNotFoundError:
    data = {"$schema": "https://pi.dev/schemas/models.schema.json"}
provider = data.setdefault("providers", {}).setdefault("llamacpp", {})
key = os.environ["API_KEY"]
before = dict(provider)
provider.update({"baseUrl": os.environ["LLAMA_URL"] + "/v1", "api": "openai-completions", "apiKey": key})
models = [{"id": os.environ["MODEL_ID"], "name": os.environ["MODEL_NAME"],
           "contextWindow": int(os.environ["CONTEXT"]), "maxTokens": int(os.environ["MAX_TOKENS"])}]
if provider.get("models") == models and before == provider and os.path.exists(path):
    print(f"{path} is up to date.")
else:
    provider["models"] = models
    # The API key (split mode) is yours only; "none" (single mode) isn't a secret.
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if key != "none" else 0o644), "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    if key != "none":
        os.chmod(path, 0o600)
    print(f"Wrote {path} (model {os.environ['MODEL_ID']}: {os.environ['MODEL_NAME']}"
          f"{', with the API key' if key != 'none' else ''})")
EOF_PY
