#!/usr/bin/env bash
# Tailscale, the private network between the main PC, the helper PC and your other devices (README, "Two-PC setup").
# Installs it, starts its service at boot, lets you use `tailscale serve` without sudo (./localllm.sh needs it in split
# mode), and signs in when this PC isn't signed in yet: the one manual part, a link to open in the browser.
# Safe to run again. In single mode nothing uses Tailscale yet; it's ready for when the helper PC comes.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

if pacman -Q tailscale >/dev/null 2>&1; then
    echo "Tailscale is installed ($(pacman -Q tailscale | cut -d' ' -f2))."
else
    sudo pacman -S --needed tailscale
fi

# Share DNS between NetworkManager and Tailscale through systemd-resolved, so NetworkManager doesn't overwrite
# /etc/resolv.conf on Wi-Fi reconnects and break MagicDNS (*.ts.net names; Tailscale's "dns-fight").
dns_changed=false
if ! systemctl is-enabled --quiet systemd-resolved || ! systemctl is-active --quiet systemd-resolved; then
    sudo systemctl enable --now systemd-resolved
    dns_changed=true
fi
if [[ "$(readlink -f /etc/resolv.conf 2>/dev/null || true)" != "/run/systemd/resolve/stub-resolv.conf" ]]; then
    sudo ln -sf /run/systemd/resolve/stub-resolv.conf /etc/resolv.conf
    dns_changed=true
    echo "Linked /etc/resolv.conf to systemd-resolved (so MagicDNS survives network changes)."
fi
if $dns_changed; then
    systemctl is-active --quiet NetworkManager && sudo systemctl restart NetworkManager || true
    systemctl is-active --quiet tailscaled && sudo systemctl restart tailscaled || true
fi

if systemctl is-enabled --quiet tailscaled && systemctl is-active --quiet tailscaled; then
    echo "tailscaled is running and starts at boot."
else
    sudo systemctl enable --now tailscaled
fi

# tailscaled may need a moment after a fresh start before it answers.
backend=""
for _ in 1 2 3 4 5; do
    backend="$(tailscale status --json 2>/dev/null \
        | python3 -c 'import json,sys; print(json.load(sys.stdin).get("BackendState",""))' 2>/dev/null || true)"
    [[ -n "$backend" ]] && break
    sleep 1
done

if [[ "$backend" == Running ]]; then
    echo "Signed in: $(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
    # You (not only root) may change `tailscale serve`; set once, kept by tailscaled.
    if ! tailscale serve status >/dev/null 2>&1; then
        sudo tailscale set --operator="$USER"
        echo "You can now run tailscale serve without sudo."
    fi
elif [[ -t 0 ]]; then
    echo "This PC isn't signed in to Tailscale yet ($backend). Scan the QR code on your phone or open the link below"
    echo "on any device (no browser needed on this PC) and sign in (the same account on every PC of this setup):"
    sudo tailscale up --qr --operator="$USER"
else
    warn "This PC isn't signed in to Tailscale yet ($backend): run install/tailscale.sh in a terminal to sign in."
fi
