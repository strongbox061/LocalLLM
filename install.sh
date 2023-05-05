#!/usr/bin/env bash
# Install Tailscale, your browser (if BROWSER_INSTALLER is set in local.env), and restore backup/.
# Safe to run again: steps that are already done are skipped.
#   ./install.sh            install what is missing
#   ./install.sh --update   also update packages
#
# Docker is not installed here: install it yourself first (pacman -S docker docker-compose,
# systemctl enable --now docker.service).
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/lib/common.sh"

UPDATE=""
if [[ "${1:-}" == "--update" ]]; then
    UPDATE=1
fi

# This setup is only installed from a backup (README, "Reinstalling from scratch"): on a machine that doesn't have its
# settings yet, backup/ must hold them (the newest archive unpacked into the repo, README "Opening a backup").
# Everything but the model takes about 28 GB; install/llama.sh checks the model's own size before downloading it.
# setup does not depend on a particular desktop environment pulling them in.

PACKAGES=(
    git
    curl
    wget
    jq
    htop
    tmux
)

# Packages this PC needs beyond the setup's own (EXTRA_PACKAGES in local.env, e.g. your laptop vendor's fan tool for
# tools/fans/). Read again after the restore, which may just have brought local.env back.
# shellcheck source=/dev/null
if [[ -f "$(dirname -- "${BASH_SOURCE[0]}")/local.env" ]]; then
    source "$(dirname -- "${BASH_SOURCE[0]}")/local.env"
fi

# --- Helper functions ---

install_packages() {
    local pkg_list=("$@")
    for pkg in "${pkg_list[@]}"; do
        if command_exists "$pkg"; then
            info "$pkg already installed, skipping"
        else
            info "Installing $pkg..."
            sudo pacman -S --noconfirm "$pkg"
        fi
    done
}

install_tailscale() {
    if command_exists tailscale; then
        info "Tailscale already installed"
    else
        info "Installing Tailscale..."
        sudo pacman -S --noconfirm tailscale
        sudo systemctl enable --now tailscaled.service
    fi
}

restore_backup() {
    local backup_dir
    backup_dir="$(dirname -- "${BASH_SOURCE[0]}")/backup"
    if [[ -d "$backup_dir" ]] && ls "$backup_dir"/*.tar.gz &>/dev/null; then
        info "Restoring from backup..."
        local latest_backup
        latest_backup=$(ls -t "$backup_dir"/*.tar.gz 2>/dev/null | head -1)
        if [[ -n "$latest_backup" ]]; then
            tar -xzf "$latest_backup" -C "$(dirname -- "${BASH_SOURCE[0]}")"
            info "Backup restored from $(basename "$latest_backup")"
        fi
    else
        info "No backup found, skipping restore"
    fi
}

install_browser() {
    if [[ -n "${BROWSER_INSTALLER:-}" ]]; then
        info "Installing browser: $BROWSER_INSTALLER"
        case "$BROWSER_INSTALLER" in
            firefox)
                sudo pacman -S --noconfirm firefox
                ;;
            chromium)
                sudo pacman -S --noconfirm chromium
                ;;
            *)
                warn "Unknown BROWSER_INSTALLER: $BROWSER_INSTALLER"
                ;;
        esac
    fi
}

# --- Main ---

if [[ "$UPDATE" == "1" ]]; then
    info "Updating all packages..."
    sudo pacman -Syu --noconfirm
fi

info "Installing base packages..."
install_packages "${PACKAGES[@]}"

# Add extra packages from local.env if set
if [[ -n "${EXTRA_PACKAGES:-}" ]]; then
    IFS=' ' read -ra extra_pkgs <<< "$EXTRA_PACKAGES"
    install_packages "${extra_pkgs[@]}"
fi

restore_backup
install_tailscale
install_browser

info "Setup complete. Next steps: install Docker, then run ./start.sh to launch services."
