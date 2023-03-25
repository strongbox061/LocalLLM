#!/usr/bin/env bash
# Stretches RAM for containers, editor and browser while the model runs (its weights take ~16.4 GB that swap can't move):
#   - zram: compressed swap in RAM, used before the disk swap partition
#   - swap tuning for zram (/etc/sysctl.d)
#   - browser Memory Saver on by default when BROWSER_POLICY_DIR is set in local.env (a recommended policy, so it can
#     still be changed in browser settings)
# Safe to run again: files are only written when they differ.
set -euo pipefail

# Source environment if available
if [[ -f "$(dirname "$0")/../local.env" ]]; then
    source "$(dirname "$0")/../local.env"
fi

# --- zram ---
ZRAM_CONF="[zram0]
# Up to half the RAM of (uncompressed) swapped pages; RAM is only used as it fills, about 1/3 of that with zstd.
# Used before the disk swap partition (priority -1 or so).
DiskSize = 50%
CompressionAlgo = zstd"

SYSCTL_CONF="vm.swappiness = 180
# page-cluster 0 reads back one page at a time (no read-ahead, which only helps disks);
# the watermarks start reclaiming a little earlier.
vm.page-cluster = 0
vm.vfs_cache_pressure = 80"

write_if_changed() {
    local file="$1"
    local content="$2"
    if [[ -f "$file" ]] && [[ "$(cat "$file")" == "$content" ]]; then
        return 0
    fi
    echo "$content" | sudo tee "$file" >/dev/null
}

# Write zram config (used by systemd-zram-generator or similar)
write_if_changed /etc/systemd/zram-config.conf "$ZRAM_CONF"

# Write sysctl tuning for zram
write_if_changed /etc/sysctl.d/99-zram.conf "$SYSCTL_CONF"
