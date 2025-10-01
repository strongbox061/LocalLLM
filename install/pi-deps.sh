#!/usr/bin/env bash
# install/pi-deps.sh — Install Node.js dependencies for Pi agent extensions and tools.
# The search-gateway extension (pi/extensions/search-gateway.ts) handles web searching directly,
# so no separate search package is installed. Dependencies are split into two tiers:
#   install/pi-deps.sh              basic deps (extensions, metrics, themes)
#   install/pi-packages.sh          only what is missing
#   install/pi-packages.sh --update also update the installed packages

set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

SETTINGS="$PI_AGENT_DIR/settings.json"

# Packages that have been superseded and should be cleaned up.
RETIRED_PACKAGES=(npm:pi-search-hub)

# Default packages installed when no settings.json override exists.
DEFAULT_PACKAGES=(
    npm:pi-sandbox
    npm:pi-metrics
    npm:pi-tool-repair
    npm:pi-mcp-adapter
    npm:awesome-pi-themes
    npm:pi-ui-extension
    npm:pi-clear-screen
    npm:pi-codemode-compact
    # npm:billion-context
    npm:modern-web-guidance
    npm:pi-simplify
)

# Resolved package list: settings.json decides which packages to install; otherwise the default list.
PACKAGES=()

# Check if a package name matches any retired package.
is_retired() {
  local p
  for p in "${RETIRED_PACKAGES[@]}"; do
    [[ "$1" == "$p" ]] && return 0
  done
  return 1
}

# recorded_version <npm name>: its version in the last backup (PI_PACKAGE_VERSIONS in versions.env), if any.
recorded_version() {
  local ver
  ver=$(grep "^${2:-}" "$PI_AGENT_DIR/versions.env" 2>/dev/null | head -1 | cut -d= -f2) || true
  echo "$ver"
}

# Read settings.json to determine which packages to install.
if [[ -f "$SETTINGS" ]]; then
  PACKAGES=($(python3 -c "
import json, sys
try:
    with open('$SETTINGS') as f:
        data = json.load(f)
    pkgs = data.get('packages', [])
    for p in pkgs:
        print(p)
except (json.JSONDecodeError, KeyError):
    pass
"))
fi

# Fall back to defaults if no packages were resolved.
if [[ ${#PACKAGES[@]} -eq 0 ]]; then
  PACKAGES=("${DEFAULT_PACKAGES[@]}")
fi

# Install each package.
for pkg in "${PACKAGES[@]}"; do
  if is_retired "$pkg"; then
    notice "Skipping retired package: $pkg"
    continue
  fi
  info "Installing dependency: $pkg"
  npm install --prefix "$PI_AGENT_DIR" "$pkg" 2>&1 | tee -a "$PI_AGENT_DIR/install.log"
done

# Clean up any retired packages that may still be installed.
for pkg in "${RETIRED_PACKAGES[@]}"; do
  if [[ -d "$PI_AGENT_DIR/node_modules/$pkg" ]]; then
    notice "Removing retired package: $pkg"
    rm -rf "$PI_AGENT_DIR/node_modules/$pkg"
  fi
done

# Sync custom browser executable, launch args, and extensions from local.env
if [[ -f "$PI_AGENT_DIR/local.env" ]]; then
  info "Syncing browser config from local.env"
  # Browser settings are read by extensions at runtime.
fi

# pi-search-hub's config, no longer read by anything.
# Its data directory can be removed if it still exists.
if [[ -d "$PI_AGENT_DIR/data/pi-search-hub" ]]; then
  notice "Cleaning up retired pi-search-hub data"
  rm -rf "$PI_AGENT_DIR/data/pi-search-hub"
fi

info "All dependencies installed successfully."
