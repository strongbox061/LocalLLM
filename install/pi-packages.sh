#!/usr/bin/env bash
# Installs Pi's npm packages and agent-browser. Pi's web_search is this repo's own extension
# (pi/extensions/search-gateway.ts, copied by install/pi-config.sh), so no search package is installed.
#   install/pi-packages.sh            only what is missing
#   install/pi-packages.sh --update   also update the installed packages
#   A package or agent-browser installed new gets the version recorded by the last backup (versions.env), if any.

set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

SETTINGS="$PI_AGENT_DIR/settings.json"

# Replaced by pi/extensions/search-gateway.ts: it searched DuckDuckGo and SearXNG directly.
RETIRED_PACKAGES=(npm:pi-search-hub)

DEFAULT_PACKAGES=(
    npm:pi-sandbox
    npm:pi-metrics
    npm:pi-agent-browser-native
    npm:pi-tool-repair
    npm:@plannotator/pi-extension
    npm:pi-mcp-adapter
    npm:awesome-pi-themes
    npm:pi-ui-extension
    npm:pi-clear-screen
    npm:pi-codemode-compact
    # npm:billion-context
    npm:modern-web-guidance
    npm:pi-simplify
)

# A restored settings.json decides which packages to install; otherwise the default list.
PACKAGES=()
if [[ -f "$SETTINGS" ]]; then
    mapfile -t PACKAGES < <(python3 -c '
import json, sys
for p in json.load(open(sys.argv[1])).get("packages", []):
    print(p if isinstance(p, str) else p.get("source", ""))
' "$SETTINGS" | grep .) || true
fi
[[ ${#PACKAGES[@]} -gt 0 ]] || PACKAGES=("${DEFAULT_PACKAGES[@]}")

is_retired() { local p; for p in "${RETIRED_PACKAGES[@]}"; do [[ "$1" == "$p" ]] && return 0; done; return 1; }

# recorded_version <npm name>: its version in the last backup (PI_PACKAGE_VERSIONS in versions.env), if any.
recorded_version() {
    local entry
    for entry in ${PI_PACKAGE_VERSIONS:-}; do
        [[ "${entry%@*}" == "$1" ]] && { echo "${entry##*@}"; return 0; }
    done
}

for package in "${PACKAGES[@]}"; do
    is_retired "$package" && continue
    if [[ "$package" == npm:* ]] && [[ -d "$PI_AGENT_DIR/npm/node_modules/${package#npm:}" ]]; then
        echo "Already installed: $package"
    else
        pi install "$package"
        # A fresh install gets the version of the last backup (settings keep the unpinned name, so --update still
        # moves it to the newest).
        name="${package#npm:}"
        version="$(recorded_version "$name")"
        if [[ "$package" == npm:* && -n "$version" && "${1:-}" != "--update" ]]; then
            npm --prefix "$PI_AGENT_DIR/npm" install --no-audit --no-fund "$name@$version" >/dev/null
            echo "  $name: the recorded version $version"
        fi
    fi
done

# A settings.json from an older backup can still list a retired package.
for package in "${RETIRED_PACKAGES[@]}"; do
    if grep -qF "\"$package\"" "$SETTINGS" 2>/dev/null || [[ -d "$PI_AGENT_DIR/npm/node_modules/${package#npm:}" ]]; then
        pi remove "$package"
        echo "Removed: $package (replaced by this repo's extensions)"
    fi
done

npm config set prefix "$HOME/.local"
npm config set allow-scripts=agent-browser --location=user
if [[ "${1:-}" == "--update" ]]; then
    npm install -g agent-browser
    pi update --extensions
elif ! command -v agent-browser >/dev/null; then
    npm install -g "agent-browser${AGENT_BROWSER_VERSION:+@$AGENT_BROWSER_VERSION}"
fi

# pi-agent-browser-native refuses an agent-browser older than its minimum (Pi can't open pages then). Pi updates its
# packages with `pi update`, and a version recorded by an older backup can be too old: install the version it targets.
BROWSER_TARGET="$PI_AGENT_DIR/npm/node_modules/pi-agent-browser-native/scripts/agent-browser-target.mjs"
if [[ -f "$BROWSER_TARGET" ]]; then
    minimum=$(sed -n 's/.*MINIMUM_AGENT_BROWSER_VERSION *= *"\([0-9.]*\)".*/\1/p' "$BROWSER_TARGET")
    target=$(sed -n 's/.*TARGET_AGENT_BROWSER_VERSION *= *"\([0-9.]*\)".*/\1/p' "$BROWSER_TARGET")
    installed=$(agent-browser --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)
    if [[ -n "$minimum" && -n "$installed" ]] &&
        [[ "$(printf '%s\n' "$minimum" "$installed" | sort -V | head -1)" != "$minimum" ]]; then
        echo "agent-browser $installed is older than pi-agent-browser-native needs ($minimum): installing ${target:-$minimum}"
        npm install -g "agent-browser@${target:-$minimum}"
    fi
fi

# pi-search-hub's config, no longer read by anything.
if [[ -f "$PI_AGENT_DIR/extensions/search.json" ]]; then
    rm -f "$PI_AGENT_DIR/extensions/search.json"
    echo "Removed: $PI_AGENT_DIR/extensions/search.json"
fi

info "All dependencies installed successfully."
