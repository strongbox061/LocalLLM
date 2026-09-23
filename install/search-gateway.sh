#!/usr/bin/env bash
# Sets up the search gateway, the one place every web search of Pi and Open WebUI goes through
# (services/search_gateway.py, port 8090), without needing Open WebUI:
#   - its own Python environment (services/search-gateway-requirements.txt, with uv) and Playwright's Chromium
#   - the search-gateway user service (not enabled; start.sh starts it), restarted if it's running
#   - from an older setup: the search settings and usage counts, cache and log are moved out of Open WebUI
#     (services/search_gateway_migrate.py); install/open-webui-config.sh then removes Open WebUI's copy of the keys
# Settings: http://127.0.0.1:8090/settings (saved to ~/.config/localllm/search-gateway.json). Usage: /dashboard.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

command -v uv >/dev/null || die "uv is missing (it installs Open WebUI too): see the README's first steps."
# The engine's word rules are generated from lang/phrases.json: never run a stale copy.
python3 "$REPO_DIR/lang/build.py" --check >/dev/null || die "lang/phrases.json changed: run python3 lang/build.py first."

# --- Python environment ---
REQ="$REPO_DIR/services/search-gateway-requirements.txt"
if [[ ! -x "$SEARCH_GATEWAY_PYTHON" ]]; then
    uv venv --quiet --python 3.11 "$SEARCH_GATEWAY_VENV"
    echo "Made the gateway's Python environment: $SEARCH_GATEWAY_VENV"
fi
uv pip install --quiet --python "$SEARCH_GATEWAY_PYTHON" -r "$REQ"
# Page reading uses Chromium through Playwright (a download of about 150 MB the first time; shared with Open WebUI's).
"$SEARCH_GATEWAY_PYTHON" -m playwright install chromium >/dev/null
echo "Python environment ready ($(grep -cv '^#' "$REQ") packages from $(basename -- "$REQ"))"

# --- moving an older setup's settings and state out of Open WebUI ---
"$SEARCH_GATEWAY_PYTHON" "$REPO_DIR/services/search_gateway_migrate.py" "$WEBUI_DATA_DIR/webui.db"

# --- service ---
# Called web-research.service before the rename: stop and remove it (same port).
if [[ -f "$HOME/.config/systemd/user/web-research.service" ]]; then
    systemctl --user disable --now web-research.service 2>/dev/null || true
    rm -f -- "$HOME/.config/systemd/user/web-research.service"
    systemctl --user daemon-reload
    echo "Removed the old web-research.service (now search-gateway.service)."
fi
install_user_unit search-gateway.service \
    "$(sed "s|@PYTHON@|$SEARCH_GATEWAY_PYTHON|; s|@REPO_DIR@|$REPO_DIR|" "$REPO_DIR/services/search-gateway.service")"
if systemctl --user is-active --quiet search-gateway; then
    systemctl --user restart search-gateway
    echo "Restarted the running search gateway."
fi

[[ -f "$SEARCH_GATEWAY_SETTINGS" ]] \
    || warn "No search settings yet: once it runs (start/search-gateway.sh), enter your API keys at $SEARCH_GATEWAY_URL/settings"
echo "Settings (once started): $SEARCH_GATEWAY_URL/settings   Usage: $SEARCH_GATEWAY_URL/dashboard"
echo "Chat types of the protected chats (once started): $SEARCH_GATEWAY_URL/chat-types"
