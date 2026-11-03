#!/usr/bin/env bash
# Pushes the tools and functions in open-webui/ into the running Open WebUI (only their code; Valves, names and
# on/off state stay as set in Open WebUI). Run it again after editing anything in open-webui/.
#   open-webui/tools/<id>.py      -> Workspace > Tools
#   open-webui/functions/<id>.py  -> Admin Panel > Functions
# It also moves the model's settings and chats from an old alias to "local" (the profile's OLD_ALIASES), once, and
# removes the search settings from Open WebUI once the search gateway has them (~/.config/localllm/search-gateway.json).
# It also sets document search for every chat: a multilingual embedding model and hybrid search (open-webui/sync.py).
# Then install/chat-types.sh builds the protected chats from ~/.config/localllm/chat-types/.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

[[ -x "$WEBUI_PYTHON" ]] || die "Open WebUI is not installed: run install/open-webui.sh first."
curl -fsS -m 3 -o /dev/null "$WEBUI_URL/health" 2>/dev/null || die "Open WebUI is not running: run start/open-webui.sh first."
[[ -f "$WEBUI_KEY_FILE" ]] || die "No $WEBUI_KEY_FILE (Open WebUI creates it on its first start)."

source "$REPO_DIR/lib/model.sh"
load_model_profile || exit 1
write_model_json >/dev/null
"$WEBUI_PYTHON" "$REPO_DIR/open-webui/sync.py" "$WEBUI_URL" "$WEBUI_DATA_DIR/webui.db" "$WEBUI_KEY_FILE" \
    "$MODEL_JSON_FILE"
