#!/usr/bin/env bash
# (pi/extensions/search-gateway.ts, copied by install/pi-config.sh), so no search package is installed.
#   install/pi-packages.sh            only what is missing
#   install/pi-packages.sh --update   also update the installed packages

set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

SETTINGS="$PI_AGENT_DIR/settings.json"

# Replaced by pi/extensions/search-gateway.ts: it searched DuckDuckGo and SearXNG directly.
RETIRED_PACKAGES=(npm:pi-search-hub)

DEFAULT_PACKAGES=(
    npm:pi-sandbox
    npm:pi-metrics
    npm:pi-mcp-adapter
    npm:pi-tool-repair
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

# pi-search-hub's config, no longer read by anything.
if [[ -f "$PI_AGENT_DIR/extensions/search.json" ]]; then
    rm -f "$PI_AGENT_DIR/extensions/search.json"
    echo "Removed: $PI_AGENT_DIR/extensions/search.json"
fi

info "All dependencies installed successfully."
