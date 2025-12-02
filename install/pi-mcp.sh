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

# --- Key declarations / sections / selectors (bodies omitted for v1) ---
SOURCE_FILE="$REPO_DIR/pi/mcp-adapter.json"
TARGET_FILE="$PI_AGENT_DIR/mcp-adapter.json"
# An earlier version also copied the key into the login session; Pi reads local.env itself now.
OLD_KEY_ENV_FILE="$HOME/.config/environment.d/61-localllm-mcp-key.conf"
# --- the encrypted sign-in file's key: made once, kept in local.env (and so in the backup) ---
# --- sign-in state: doctor connects to each server and never starts a sign-in itself ---
DOCTOR="$PI_AGENT_DIR/npm/node_modules/.bin/pi-mcp-adapter"

setup_mcp_adapter() {
  # Ensure target directory exists
  mkdir -p "$(dirname "$TARGET_FILE")"

  # Copy the adapter config
  if [[ -f "$SOURCE_FILE" ]]; then
    cp "$SOURCE_FILE" "$TARGET_FILE"
    echo "Copied MCP adapter config to $TARGET_FILE"
  else
    echo "Warning: Source file $SOURCE_FILE not found"
    return 1
  fi
}

generate_mcp_key() {
  # Generate a key for Context7 sign-in if one doesn't exist
  if [[ ! -f "$MCP_KEY_FILE" ]]; then
    MCP_KEY=$(openssl rand -hex 32)
    echo "MCP_CONTEXT7_KEY=$MCP_KEY" >> "$LOCAL_ENV_FILE"
    echo "Generated new MCP key in $LOCAL_ENV_FILE"
  else
    echo "MCP key already exists at $MCP_KEY_FILE"
  fi
}

check_context7_status() {
  # Use the doctor to check if Context7 is signed in
  if command -v "$DOCTOR" &>/dev/null; then
    "$DOCTOR" --check context7 2>/dev/null && echo "Context7: signed in" || echo "Context7: not signed in"
  else
    echo "Doctor not found at $DOCTOR"
  fi
}

# Main execution
setup_mcp_adapter || true
generate_mcp_key || true
check_context7_status
