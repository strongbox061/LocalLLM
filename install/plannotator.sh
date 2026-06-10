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

PI_AGENT_DIR="${PI_AGENT_DIR:-$HOME/.pi}"
PLANNODATORDIR="$PI_AGENT_DIR/tools"
EXT_PACKAGE="$PI_AGENT_DIR/npm/node_modules/@plannotator/pi-extension/package.json"

# --- version ---
if [ ! -f "$EXT_PACKAGE" ]; then
  echo "ERROR: @plannotator/pi-extension not found at $EXT_PACKAGE" >&2
  echo "Run install/pi-packages.sh first." >&2
  exit 1
fi

VERSION="$(node -p 'require(process.argv[1]).version' "$EXT_PACKAGE")"
echo "Installing plannotator v$VERSION"

# --- the binary ---
BINARY="$PLANNODATORDIR/plannotator"
if [ -f "$BINARY" ]; then
  echo "plannotator binary already installed, skipping."
else
  mkdir -p "$PLANNODATORDIR"
  npm install -g --prefix /tmp/plando-install "@plannotator/pi-extension@$VERSION" --minimal 2>/dev/null || true
  # The npm global bin puts the executable in node_modules/.bin/
  BIN_SRC="/tmp/plando-install/lib/node_modules/@plannotator/pi-extension/dist/cli.js"
  if [ -f "$BIN_SRC" ]; then
    cp "$BIN_SRC" "$BINARY"
    chmod +x "$BINARY"
    echo "Installed binary to $BINARY"
  else
    # Fallback: link the npm bin directly
    LINK="/tmp/plando-install/bin/plannotator"
    if [ -f "$LINK" ]; then
      cp "$LINK" "$BINARY"
      chmod +x "$BINARY"
      echo "Installed binary to $BINARY (npm bin)"
    else
      echo "WARNING: could not find plannotator binary in npm package, trying direct install..."
      npm install -g --minimal "@plannotator/pi-extension@$VERSION" 2>/dev/null || true
      NPM_BIN="$(npm config get prefix)/bin/plannotator"
      if [ -f "$NPM_BIN" ]; then
        cp "$NPM_BIN" "$BINARY"
        chmod +x "$BINARY"
        echo "Installed binary to $BINARY (global npm)"
      else
        echo "ERROR: plannotator binary not found after npm install." >&2
        exit 1
      fi
    fi
  fi
  rm -rf /tmp/plando-install
fi

# --- Pi's plannotator_inbox tool ---
# Off by default for Pi, because its definition adds about 3,200 tokens to every Pi request. Only this key is set; the
# rest of config.json (the Inbox's own settings) is kept. Pi picks it up in sessions started after the Inbox has run
# once (`plannotator inbox`), or after /reload.

CONFIG_FILE="$PI_AGENT_DIR/config.json"
TOOL_CODE='const fs = require("fs");
const path = require("path");
const file = process.argv[2];
let config = {};
const tool = config.inboxTool;
if (tool === true || (tool && typeof tool === "object" && tool.pi === true)) {'

if [ ! -f "$CONFIG_FILE" ]; then
  echo '{}' > "$CONFIG_FILE"
fi

# Check if inboxTool is already configured
if node -e "const c=JSON.parse(require('fs').readFileSync('$CONFIG_FILE','utf8')); process.exit(c.inboxTool ? 0 : 1)" 2>/dev/null; then
  echo "inboxTool already configured, skipping."
else
  # Add inboxTool: true to config.json using node for safe JSON manipulation
  node -e "
    const fs = require('fs');
    let c = JSON.parse(fs.readFileSync('$CONFIG_FILE', 'utf8'));
    c.inboxTool = true;
    fs.writeFileSync('$CONFIG_FILE', JSON.stringify(c, null, 2) + '\n');
  "
  echo "Configured inboxTool in $CONFIG_FILE"
fi

echo "Plannotator v$VERSION installed successfully."
echo "Start the Inbox with: plannotator inbox"
