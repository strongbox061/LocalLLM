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

# --- Service ---
# Called web-research.service before the rename: stop and remove it (same port).
if systemctl --user is-active "$SERVICE_NAME" &>/dev/null; then
    echo "Stopping existing $SERVICE_NAME service..."
    systemctl --user stop "$SERVICE_NAME"
    systemctl --user disable "$SERVICE_NAME"
fi

# Create the systemd user service file
mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/$SERVICE_NAME.service" <<EOF
[Unit]
Description=LocalLLM Search Gateway
After=network.target

[Service]
Type=simple
ExecStart=$VENV_DIR/bin/python $REPO_DIR/services/search_gateway.py --port $PORT
WorkingDirectory=$REPO_DIR
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
echo "Search gateway service configured. Start it with: systemctl --user start $SERVICE_NAME"
echo "Access settings at: http://127.0.0.1:$PORT/settings"
