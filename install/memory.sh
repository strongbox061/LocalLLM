#!/usr/bin/env bash
# Stretches RAM for containers, editor and browser while the model runs (its weights take ~16.4 GB that swap can't move):
#   - zram: compressed swap in RAM, used before the disk swap partition
#   - swap tuning for zram (/etc/sysctl.d)
#   - browser Memory Saver on by default when BROWSER_POLICY_DIR is set in local.env (a recommended policy, so it can
#     still be changed in browser settings)
# Safe to run again: files are only written when they differ.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

# --- zram ---
pacman -Q zram-generator >/dev/null 2>&1 || sudo pacman -S --needed zram-generator

ZRAM_CONF="[zram0]
# Up to half the RAM of (uncompressed) swapped pages; RAM is only used as it fills, about 1/3 of that with zstd.
zram-size = ram / 2
compression-algorithm = zstd
# Used before the disk swap partition (priority -1 or so).
swap-priority = 100"

if install_system_file /etc/systemd/zram-generator.conf "$ZRAM_CONF"; then
    sudo systemctl daemon-reload
    sudo systemctl restart systemd-zram-setup@zram0.service
fi

# --- swap tuning for zram ---
# swappiness above 100 tells the kernel swapping to zram is cheaper than dropping file cache; page-cluster 0 reads
# back one page at a time (no read-ahead, which only helps disks); the watermarks start reclaiming a little earlier.
SYSCTL_CONF="vm.swappiness = 180
vm.page-cluster = 0
vm.watermark_boost_factor = 0
vm.watermark_scale_factor = 125"

if install_system_file /etc/sysctl.d/99-localllm-zram.conf "$SYSCTL_CONF"; then
    sudo sysctl --system >/dev/null
    echo "Applied the swap settings."
fi

# --- Browser Memory Saver (optional, when BROWSER_POLICY_DIR is set in local.env) ---
# Discards background tabs to free their memory. Written even before the browser is installed, so it applies once it is.
if [[ -n "${BROWSER_POLICY_DIR:-}" ]]; then
    install_system_file "${BROWSER_POLICY_DIR%/}/recommended/localllm-memory.json" '{ "HighEfficiencyModeEnabled": true }' \
        || true
fi

# --- result ---
echo
swapon --show
if ! swapon --show=NAME --noheadings | grep -qv '^/dev/zram'; then
    warn "No disk swap besides zram. It is optional, but it is the last resort when zram is full;" \
        "it comes from the OS install (a swap partition or swap file), not from this repo."
fi
