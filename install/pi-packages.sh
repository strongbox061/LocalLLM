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

# Sync custom browser executable, launch args, and extensions from local.env
# (AGENT_BROWSER_EXECUTABLE_PATH, AGENT_BROWSER_ARGS, AGENT_BROWSER_EXTENSIONS) into ~/.agent-browser/config.json.
AGENT_BROWSER_CONFIG_FILE="$HOME/.agent-browser/config.json"
python3 - "$AGENT_BROWSER_CONFIG_FILE" "${AGENT_BROWSER_EXECUTABLE_PATH:-}" "${AGENT_BROWSER_ARGS:-}" "${AGENT_BROWSER_EXTENSIONS[@]}" <<'EOF_PY'
import json, os, sys
path = sys.argv[1]
exe = sys.argv[2].strip()
args = sys.argv[3].strip()
exts = [os.path.expanduser(e.strip()) for e in sys.argv[4:] if e.strip()]
try:
    with open(path) as f:
        cfg = json.load(f)
except FileNotFoundError:
    cfg = None
except ValueError as e:
    sys.exit(f"ERROR: {path} is not valid JSON ({e}); fix it, then run this again.")

changed = False
if exe:
    cfg = cfg if isinstance(cfg, dict) else {}
    if cfg.get("executablePath") == exe:
        print(f"Same: {path} executablePath ({exe})")
    else:
        cfg["executablePath"] = exe
        changed = True
        print(f"Set: {path} executablePath ({exe})")
elif isinstance(cfg, dict) and "executablePath" in cfg:
    del cfg["executablePath"]
    changed = True
    print(f"Cleared: {path} executablePath (using agent-browser's own Chromium)")

if args:
    cfg = cfg if isinstance(cfg, dict) else {}
    if cfg.get("args") == args:
        print(f"Same: {path} args ({args})")
    else:
        cfg["args"] = args
        changed = True
        print(f"Set: {path} args ({args})")
elif isinstance(cfg, dict) and "args" in cfg:
    del cfg["args"]
    changed = True
    print(f"Cleared: {path} args")

if exts:
    for ext in exts:
        if not os.path.isdir(ext):
            print(f"WARNING: agent-browser extension directory does not exist: {ext}", file=sys.stderr)
    cfg = cfg if isinstance(cfg, dict) else {}
    if cfg.get("extensions") == exts:
        print(f"Same: {path} extensions ({', '.join(exts)})")
    else:
        cfg["extensions"] = exts
        changed = True
        print(f"Set: {path} extensions ({', '.join(exts)})")
elif isinstance(cfg, dict) and "extensions" in cfg:
    del cfg["extensions"]
    changed = True
    print(f"Cleared: {path} extensions")

if changed:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
EOF_PY

if [[ -z "${AGENT_BROWSER_EXECUTABLE_PATH:-}" ]]; then
    agent-browser install
fi
agent-browser doctor || warn "agent-browser doctor reported problems (see above)."

# pi-search-hub's config, no longer read by anything.
if [[ -f "$PI_AGENT_DIR/extensions/search.json" ]]; then
    rm -f "$PI_AGENT_DIR/extensions/search.json"
    echo "Removed: $PI_AGENT_DIR/extensions/search.json"
fi
