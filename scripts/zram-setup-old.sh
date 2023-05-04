#!/usr/bin/env bash
set -euo pipefail

# zram-setup-old.sh — Manual zram swap setup for local LLM workflows
# Creates a compressed block device and uses it as swap to reduce RAM pressure.

ZRAM_SIZE="${ZRAM_SIZE:-2G}"
ZRAM_ALGO="${ZRAM_ALGO:-lzo}"

setup_zram() {
    modprobe zram num_devices=1

    echo "$ZRAM_ALGO" > /sys/block/zram0/comp_algorithm
    echo "$ZRAM_SIZE" > /sys/block/zram0/disksize
    mkswap /dev/zram0
    swapon /dev/zram0

    echo "zram swap active: /dev/zram0 (${ZRAM_SIZE}, algo=${ZRAM_ALGO})"
}

teardown_zram() {
    swapoff /dev/zram0 2>/dev/null || true
    echo 1 > /sys/block/zram0/reset
    echo "zram swap removed"
}

case "${1:-}" in
    teardown)
        teardown_zram
        ;;
    *)
        setup_zram
        ;;
esac
