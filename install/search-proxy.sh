#!/usr/bin/env bash
# Sets up the search gateway, the one place every web search of Pi and Open WebUI goes through
# (services/search_gateway.py, port 8090), without needing Open WebUI:
#   - its own Python environment (services/search-gateway-requirements.txt, with uv) and Playwright's Chromium
#   - the search-gateway user service (not enabled; start.sh starts it), restarted if it's running
#   - from an older setup: the search settings and usage counts, cache and log are moved out of Open WebUI
#     (services/search_gateway_migrate.py); install/open-webui-config.sh then removes Open WebUI's copy of the keys
# Settings: http://127.0.0.1:8090/settings (saved to ~/.config/localllm/search-gateway.json). Usage: /dashboard.

set -euo pipefail

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
CONFIG_DIR="$HOME/.config/localllm"
CONFIG_FILE="$CONFIG_DIR/search-gateway.json"
SERVICE_NAME="search-gateway"
PORT=8090

# --- Python environment ---
REQ="$REPO_DIR/services/search-gateway-requirements.txt"

if ! command -v uv &>/dev/null; then
    echo "Error: uv is required but not installed. Install it with: pip install uv" >&2
    exit 1
fi

VENV_DIR="$REPO_DIR/services/.search-gateway-venv"

if [[ ! -d "$VENV_DIR" ]]; then
    echo "Creating Python virtual environment for search gateway..."
    python3 -m venv "$VENV_DIR"
fi

echo "Installing search gateway dependencies..."
"$VENV_DIR/bin/uv" pip install -r "$REQ"

# Playwright's Chromium (about 150 MB on first download; shared with Open WebUI)
if ! "$VENV_DIR/bin/playwright" install --with-deps chromium &>/dev/null; then
    echo "Warning: Playwright Chromium installation had issues. You may need to run it manually." >&2
fi

# --- Moving an older setup's settings and state out of Open WebUI ---
if [[ -f "$REPO_DIR/services/.open-webui-search-config" ]]; then
    echo "Migrating search settings from older Open WebUI setup..."
    mkdir -p "$CONFIG_DIR"
    cp "$REPO_DIR/services/.open-webui-search-config" "$CONFIG_FILE"
    rm "$REPO_DIR/services/.open-webui-search-config"
    echo "Settings migrated to $CONFIG_FILE"
fi

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
Description=LocalLLM Search Gateway Proxy
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
