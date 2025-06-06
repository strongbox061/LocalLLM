#!/usr/bin/env bash
# Installs Open WebUI with uv and its user service (not enabled). Start it with ./start.sh.
#   install/open-webui.sh            only if it is missing
#   install/open-webui.sh --update   reinstall the latest version
#   A fresh install uses the version recorded by the last backup (~/.config/localllm/versions.env), if any.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

if [[ "${1:-}" == "--update" ]]; then
    uv tool install --force --python 3.11 open-webui --with playwright --with langdetect
elif [[ ! -x "$HOME/.local/bin/open-webui" ]]; then
    # The version of the last backup (versions.env), if there is one.
    uv tool install --force --python 3.11 "open-webui${OPEN_WEBUI_VERSION:+==$OPEN_WEBUI_VERSION}" --with playwright --with langdetect
else
    echo "Open WebUI is already installed."
fi

# The web loader (WEB_LOADER_ENGINE=playwright in services/open-webui.sh) needs Chromium.
"$WEBUI_PYTHON" -m playwright install chromium

install_user_unit open-webui.service "$(sed "s|@REPO_DIR@|$REPO_DIR|" "$REPO_DIR/services/open-webui.service")"
