#!/usr/bin/env bash
# Pi's documentation sources (MCP servers, through the pi-mcp-adapter package): GitMCP for a GitHub project's live docs
# and Context7 for a library's docs at a specific version. Copies pi/mcp-adapter.json to ~/.pi/agent/mcp-adapter.json.
#
# Context7 needs a sign-in (/mcp-auth context7 in an interactive Pi session, approved in the browser). The adapter keeps
# it in its own encrypted file (~/.pi/agent/mcp-oauth-encrypted/, part of the settings backup), so no desktop keyring
# is involved. Its key is made here once and kept in ~/.config/localllm/local.env (also in the backup); Pi loads it
# from there itself when it starts (pi/extensions/mcp-sign-in-key.ts), so no login or session setup is needed.
# Ends by saying whether Context7 is signed in.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

SOURCE_FILE="$REPO_DIR/pi/mcp-adapter.json"
TARGET_FILE="$PI_AGENT_DIR/mcp-adapter.json"
# An earlier version also copied the key into the login session; Pi reads local.env itself now.
OLD_KEY_ENV_FILE="$HOME/.config/environment.d/61-localllm-mcp-key.conf"

[[ -f "$SOURCE_FILE" ]] || die "Not found: $SOURCE_FILE"
[[ -d "$PI_AGENT_DIR/npm/node_modules/pi-mcp-adapter" ]] \
    || warn "pi-mcp-adapter isn't installed (install/pi-packages.sh): Pi won't load these servers until it is."

if [[ -f "$TARGET_FILE" ]] && cmp -s "$SOURCE_FILE" "$TARGET_FILE"; then
    echo "Same: $TARGET_FILE"
else
    if [[ -f "$TARGET_FILE" ]]; then
        backup="$TARGET_FILE.bak.$(date +%Y%m%d-%H%M%S)"
        cp -a "$TARGET_FILE" "$backup"
        echo "Backed up existing file to: $backup"
    fi
    mkdir -p "$PI_AGENT_DIR"
    install -m 600 "$SOURCE_FILE" "$TARGET_FILE"
    echo "Wrote: $TARGET_FILE"
fi

# --- the encrypted sign-in file's key: made once, kept in local.env (and so in the backup) ---
if [[ -z "${PI_MCP_ADAPTER_OAUTH_FILE_KEY:-}" ]]; then
    mkdir -p "$(dirname -- "$LOCAL_ENV_FILE")"
    # No local.env yet (install.sh restores it from the backup first; this covers running this script on its own): start
    # it from the template, settings to fill in later.
    [[ -f "$LOCAL_ENV_FILE" ]] || install -m 600 "$REPO_DIR/config/local.example.env" "$LOCAL_ENV_FILE"
    PI_MCP_ADAPTER_OAUTH_FILE_KEY="$(node -e "console.log(require('crypto').randomBytes(32).toString('base64'))")"
    {
        echo "# The key of Pi's encrypted Context7 sign-in (made by install/pi-mcp.sh; losing it means signing in again)."
        echo "PI_MCP_ADAPTER_OAUTH_FILE_KEY=\"$PI_MCP_ADAPTER_OAUTH_FILE_KEY\""
    } >> "$LOCAL_ENV_FILE"
    chmod 600 "$LOCAL_ENV_FILE"
    export PI_MCP_ADAPTER_OAUTH_FILE_KEY
    echo "Made the key for Pi's encrypted sign-in file (in $LOCAL_ENV_FILE)."
fi
if [[ -f "$OLD_KEY_ENV_FILE" ]]; then
    rm -f "$OLD_KEY_ENV_FILE"
    echo "Removed $OLD_KEY_ENV_FILE (no longer needed: Pi reads the key from local.env)."
fi

# --- sign-in state: doctor connects to each server and never starts a sign-in itself ---
DOCTOR="$PI_AGENT_DIR/npm/node_modules/.bin/pi-mcp-adapter"
state=""
if [[ -x "$DOCTOR" ]]; then
    state="$(cd "$HOME" && timeout 60 "$DOCTOR" doctor --json </dev/null 2>/dev/null \
        | python3 -c 'import json,sys; print(next((s["state"] for s in json.load(sys.stdin) if s["name"]=="context7"), "'))' \
        2>/dev/null)" || true
fi
case "$state" in
    ok) echo "Context7: signed in. With llama-server running, tests/context7-check.sh does a real docs lookup." ;;
    needs-auth) echo "Context7: not signed in yet. Run /mcp-auth context7 in an interactive Pi session (a new one) and" \
                     "approve in the browser." ;;
    *) echo "Context7: sign-in state unknown (${state:-no answer}). If Pi says it needs a sign-in, run" \
            "/mcp-auth context7 in an interactive Pi session." ;;
esac
echo "Start a new Pi session (or /reload) to load the servers."


