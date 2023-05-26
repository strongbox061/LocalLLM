#!/usr/bin/env bash
# Tailscale, the private network between the main PC, the helper PC and your other devices (README, "Two-PC setup").
# Installs it, starts its service at boot, lets you use `tailscale serve` without sudo (./localllm.sh needs it in split
# mode), and signs in when this PC isn't signed in yet: the one manual part, a link to open in the browser.
# Safe to run again. In single mode nothing uses Tailscale yet; it's ready for when the helper PC comes.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

if pacman -Q tailscale >/dev/null 2>&1; then
  info "tailscale already installed, skipping install"
else
  info "Installing tailscale..."
  sudo pacman -S --noconfirm tailscale
fi

# Share DNS between NetworkManager and Tailscale through systemd-resolved, so NetworkManager doesn't overwrite
# /etc/resolv.conf on Wi-Fi reconnects and break MagicDNS (*.ts.net names; Tailscale's "dns-fight").
if ! grep -q "127.0.0.53" /etc/systemd/resolved.conf 2>/dev/null; then
  info "Configuring systemd-resolved for shared DNS..."
  sudo sed -i '/^#DNS=/s/^#//' /etc/systemd/resolved.conf
  sudo sed -i 's/^DNS=.*/DNS=127.0.0.53/' /etc/systemd/resolved.conf
  systemctl restart systemd-resolved
fi

# tailscaled may need a moment after a fresh start before it answers.
if ! systemctl is-active --quiet tailscaled; then
  info "Starting tailscaled..."
  sudo systemctl enable --now tailscaled
  sleep 3
fi

# Sign in if this node isn't authenticated yet
if ! tailscale status >/dev/null 2>&1; then
  info "Tailscale not signed in. Open this URL to authenticate:"
  sudo tailscale up --login-server https://login.tailscale.com
  info "Signed in successfully."
fi

info "Tailscale setup complete. You can now use 'tailscale serve' without sudo."
