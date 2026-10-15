#!/usr/bin/env bash
# Installs the plannotator program, which has the Inbox: one browser window, started by hand with `plannotator inbox`,
# where the model (through Pi) can leave messages and questions and you answer when you can. Pi's side is the
# @plannotator/pi-extension package, installed by install/pi-packages.sh; the binary is pinned to that extension's
# version so both match. Plan reviews are separate: they keep opening their own tab.
# Only the binary is installed (--minimal): the official installer otherwise also writes skills, commands and plugin
# updates for the other coding agents it finds.
# The Inbox's note in Pi's instructions is part of pi/AGENTS.md (install/pi-config.sh copies it).
# Safe to run again: nothing happens when everything is already in place.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

EXT_PACKAGE="$PI_AGENT_DIR/npm/node_modules/@plannotator/pi-extension/package.json"
[[ -f "$EXT_PACKAGE" ]] \
    || die "Pi's Plannotator extension is not installed ($EXT_PACKAGE). Run install/pi-packages.sh first."

VERSION="$(node -p 'require(process.argv[1]).version' "$EXT_PACKAGE")"

# --- the binary ---
if command -v plannotator >/dev/null && plannotator --version 2>/dev/null | grep -qF "$VERSION"; then
    echo "plannotator $VERSION is already installed."
else
    # Downloaded first, not piped: in --minimal mode the installer exits before reading all of itself, which makes a
    # piping curl fail ("Failure writing output").
    INSTALLER="$(mktemp)"
    trap 'rm -f "$INSTALLER"' EXIT
    curl -fsSL https://plannotator.ai/install.sh -o "$INSTALLER"
    bash "$INSTALLER" --version "v$VERSION" --minimal --non-interactive
    command -v plannotator >/dev/null || die "plannotator was not installed in ~/.local/bin."
    echo "Installed plannotator $VERSION."
fi

# --- Pi's plannotator_inbox tool ---
# Off by default for Pi, because its definition adds about 3,200 tokens to every Pi request. Only this key is set; the
# rest of config.json (the Inbox's own settings) is kept. Pi picks it up in sessions started after the Inbox has run
# once (`plannotator inbox`), or after /reload.
node - "$(plannotator data-dir)/config.json" <<'EOF'
const fs = require("fs");
const path = require("path");
const file = process.argv[2];
let config = {};
try { config = JSON.parse(fs.readFileSync(file, "utf8")); } catch {}
const tool = config.inboxTool;
if (tool === true || (tool && typeof tool === "object" && tool.pi === true)) {
    console.log("The Inbox tool for Pi is already on.");
} else {
    config.inboxTool = { ...(tool && typeof tool === "object" ? tool : {}), pi: true };
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, JSON.stringify(config, null, 2) + "\n", { mode: 0o600 });
    console.log(`Turned on the Inbox tool for Pi in ${file}`);
}
EOF

echo "Start the Inbox with: plannotator inbox"
