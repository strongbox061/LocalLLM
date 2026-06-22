#!/usr/bin/env bash
# Installs Pi and the llama.cpp provider in models.json (sized from the model profile), and puts ~/.local/bin on PATH
# for the desktop session from the next login (~/.config/environment.d/60-localllm-path.conf).
#   install/pi.sh            only if Pi is missing
#   install/pi.sh --update   install the latest Pi
#   A fresh install uses the version recorded by the last backup (~/.config/localllm/versions.env), if any.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

command -v npm >/dev/null || sudo pacman -S --needed nodejs npm
command -v uv >/dev/null || sudo pacman -S --needed uv

npm config set prefix "$HOME/.local"

# pi, plannotator and agent-browser live in ~/.local/bin, which Arch doesn't put on PATH. This puts it there for the
# whole desktop session (terminals included) from the next login.
ENV_FILE="$HOME/.config/environment.d/60-localllm-path.conf"
# Written literally: systemd expands ${HOME} and ${PATH} at login.
# shellcheck disable=SC2016
ENV_LINE='PATH=${HOME}/.local/bin:${PATH}'
if [[ "$(cat "$ENV_FILE" 2>/dev/null)" != "$ENV_LINE" ]]; then
    mkdir -p "$(dirname -- "$ENV_FILE")"
    printf '%s\n' "$ENV_LINE" > "$ENV_FILE"
    echo "Wrote $ENV_FILE (~/.local/bin on PATH from the next login)"
fi

if [[ "${1:-}" == "--update" ]] || ! command -v pi >/dev/null; then
    rm -f "$HOME/.local/bin/pi"
    version=""
    [[ "${1:-}" == "--update" ]] || version="${PI_VERSION:-}"
    npm install -g --ignore-scripts "@earendil-works/pi-coding-agent${version:+@$version}"
    hash -r
fi
readlink -f "$(command -v pi)"
pi --version

# ddgs (DuckDuckGo search) from older versions of this kit: Pi no longer searches DuckDuckGo directly.
DDGS_PTH="$(python3 -m site --user-site)/pi-ddgs.pth"
if [[ -f "$DDGS_PTH" || -d "$HOME/.local/share/pi-ddgs" ]]; then
    rm -f "$DDGS_PTH"
    rm -rf "$HOME/.local/share/pi-ddgs"
    echo "Removed the old ddgs environment."
fi
if uv tool list 2>/dev/null | grep -q '^ddgs '; then
    uv tool uninstall ddgs
fi
if [[ -L "$HOME/.local/bin/ddgs" && ! -e "$HOME/.local/bin/ddgs" ]]; then
    rm -f "$HOME/.local/bin/ddgs"
    echo "Removed the leftover ddgs link."
fi

# The llama.cpp provider in Pi's models.json (the model profile's size; split mode's API key).
"$REPO_DIR/install/pi-models.sh"


