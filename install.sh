#!/usr/bin/env bash
# Installs everything: llama.cpp + model, SearXNG, Pi + extensions, Open WebUI, OCR, Kokoro, zram and swap tuning,
# Tailscale, your browser (if BROWSER_INSTALLER is set in local.env), and restores backup/.
# Safe to run again: steps that are already done are skipped.
#   ./install.sh            install what is missing
#   ./install.sh --update   also update your browser (if configured), llama.cpp, Pi, its extensions and Open WebUI
# Docker is not installed here: install it yourself first (pacman -S docker docker-compose,
# systemctl enable --now docker.service).
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/lib/common.sh"

UPDATE=""
case "${1:-}" in
    "") ;;
    --update) UPDATE="--update" ;;
    *) die "Usage: $0 [--update]" ;;
esac

[[ "$(id -u)" -ne 0 ]] || die "Run this as your normal user, not root. It asks for sudo when needed."

log "Checks"
nvidia-smi >/dev/null 2>&1 || die "nvidia-smi does not work. Install the NVIDIA driver (and reboot) first."
command -v docker >/dev/null || die "Docker is not installed. Install docker and docker-compose first."
docker compose version >/dev/null 2>&1 || die "'docker compose' does not work. Install docker-compose."
systemctl is-enabled --quiet docker.service \
    || die "docker.service is not enabled, so SearXNG would not start at boot: sudo systemctl enable --now docker.service"
echo "NVIDIA driver and Docker are ready."
# This setup is only installed from a backup (README, "Reinstalling from scratch"): on a machine that doesn't have its
# settings yet, backup/ must hold them (the newest archive unpacked into the repo, README "Opening a backup").
if [[ ! -f "$LOCAL_ENV_FILE" ]]; then
    for f in localllm/local.env open-webui/webui.db; do
        [[ -f "$BACKUP_DIR/$f" ]] \
            || die "No backup to install from (backup/$f is missing): unpack the newest backup archive into $REPO_DIR first (README, \"Opening a backup\")."
    done
    echo "Installing from the backup in $BACKUP_DIR."
fi
free_gb="$(df --output=avail -BG "$HOME" | tail -1 | tr -dc '0-9')"
# Everything but the model takes about 28 GB; install/llama.sh checks the model's own size before downloading it.
if [[ "${free_gb:-0}" -lt 30 ]]; then
    warn "Only ${free_gb} GB free in $HOME; a fresh install needs about 28 GB plus the model (README, \"Reinstalling from scratch\")."
fi

log "System packages"
# setup does not depend on a particular desktop environment pulling them in.
PACKAGES=(
    base-devel cmake git cuda uv nodejs npm python diffutils sqlite openssl openssh curl gnupg rclone shellcheck bubblewrap libnotify dmidecode
    ffmpeg nss nspr at-spi2-core libcups libdrm libxkbcommon mesa pango cairo alsa-lib libxcomposite libxdamage libxfixes libxrandr
)
if pacman -Q "${PACKAGES[@]}" >/dev/null 2>&1; then
    echo "Already installed."
else
    sudo pacman -S --needed "${PACKAGES[@]}"
fi

log "Restore settings from backup/ (only what is missing)"
"$REPO_DIR/restore.sh" --missing-only

# Packages this PC needs beyond the setup's own (EXTRA_PACKAGES in local.env, e.g. your laptop vendor's fan tool for
# tools/fans/). Read again after the restore, which may just have brought local.env back.
# shellcheck source=/dev/null
[[ -f "$LOCAL_ENV_FILE" ]] && source "$LOCAL_ENV_FILE"
if refresh_endpoints force; then
    # shellcheck source=/dev/null
    source "$ENDPOINTS_ENV"
fi
if [[ "$(declare -p EXTRA_PACKAGES 2>/dev/null)" == "declare -a"* && ${#EXTRA_PACKAGES[@]} -gt 0 ]]; then
    log "This PC's extra packages (EXTRA_PACKAGES in local.env)"
    if pacman -Q "${EXTRA_PACKAGES[@]}" >/dev/null 2>&1; then
        echo "Already installed."
    else
        sudo pacman -S --needed "${EXTRA_PACKAGES[@]}"
    fi
fi

if [[ -n "${BROWSER_INSTALLER:-}" ]]; then
    log "Browser (BROWSER_INSTALLER in local.env)"
    [[ -x "$BROWSER_INSTALLER" ]] || die "BROWSER_INSTALLER is not executable: $BROWSER_INSTALLER"
    "$BROWSER_INSTALLER" $UPDATE
fi
if [[ -n "${BROWSER_POLICY_DIR:-}" ]]; then
    "$REPO_DIR/install/browser-address.sh"
fi

log "Memory: zram, swap tuning${BROWSER_POLICY_DIR:+, browser Memory Saver}"
"$REPO_DIR/install/memory.sh"

log "llama.cpp and model"
"$REPO_DIR/install/llama.sh" $UPDATE

log "SearXNG"
"$REPO_DIR/install/searxng.sh"

log "Pi"
"$REPO_DIR/install/pi.sh" $UPDATE

log "Open WebUI"
"$REPO_DIR/install/open-webui.sh" $UPDATE

log "OCR for scanned PDFs (Tesseract, OCRmyPDF)"
"$REPO_DIR/install/ocr.sh" $UPDATE

log "Search gateway (web search for Pi and Open WebUI)"
"$REPO_DIR/install/search-gateway.sh"

log "Kokoro (text-to-speech for Open WebUI)"
"$REPO_DIR/install/kokoro.sh"

log "Tailscale (the private network to the helper PC, for the two-PC setup)"
"$REPO_DIR/install/tailscale.sh"

if [[ -n "${HELPER_HOST:-}" ]]; then
    log "Helper PC Tailscale keepalive timer (install/helper-keepalive.sh --enable turns it on)"
    "$REPO_DIR/install/helper-keepalive.sh"
fi

log "Off-site backup with rclone (the timer; README step 12 turns it on)"
"$REPO_DIR/install/backup-timer.sh"

log "Done"
echo "SearXNG is running and starts at boot (Docker, restart: always)."
echo "llama-server, Open WebUI and the search gateway are installed but not started: run ./start.sh"
echo "Next, the README's steps after the scripts (\"Reinstalling from scratch\", steps 7-12): log out and in once,"
echo "./start.sh, the Context7 check (sign in only if it says so), a look at Open WebUI, the backup passphrase file,"
echo "and the scheduled backup."
