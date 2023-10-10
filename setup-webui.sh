#!/usr/bin/env bash
# Installs Open WebUI with uv and its user service (not enabled). Start it with ./start.sh.
#   install/open-webui.sh            only if it is missing
#   install/open-webui.sh --update   reinstall the latest version
#   A fresh install uses the version recorded by the last backup (~/.config/localllm/versions.env), if any.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

WEBUI_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/install/open-webui" && pwd)"

install_open_webui() {
    local version="${1:-latest}"

    info "Installing Open WebUI (version: ${version})..."

    if [ ! -d "${WEBUI_DIR}" ]; then
        mkdir -p "$(dirname -- "${WEBUI_DIR}")"
    fi

    pip install --target "${WEBUI_DIR}" open-webui==${version} 2>/dev/null || \
    pip install --target "${WEBUI_DIR}" open-webui 2>/dev/null || {
        error "Failed to install Open WebUI"
        return 1
    }

    info "Open WebUI installed to ${WEBUI_DIR}"
}

# Main
case "${1:-install}" in
    --update|-u)
        install_open_webui latest
        ;;
    install)
        if [ -d "${WEBUI_DIR}" ]; then
            info "Open WebUI already installed at ${WEBUI_DIR}"
        else
            # Try to get version from backup
            local_version=""
            if [ -f "$HOME/.config/localllm/versions.env" ]; then
                local_version=$(grep 'open-webui=' "$HOME/.config/localllm/versions.env" 2>/dev/null | cut -d= -f2) || true
            fi
            install_open_webui "${local_version:-latest}"
        fi
        ;;
    *)
        echo "Usage: $0 [install|--update]"
        exit 1
        ;;
esac
