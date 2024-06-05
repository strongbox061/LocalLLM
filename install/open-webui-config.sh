#!/usr/bin/env bash
# Pushes the tools and functions in open-webui/ into the running Open WebUI (only their code; Valves, names and
# on/off state stay as set in Open WebUI). Run it again after editing anything in open-webui/.
#   open-webui/tools/<id>.py      -> Workspace > Tools
#
#   open-webui/functions/<id>.py  -> Admin Panel > Functions
# It also moves the model's settings and chats from an old alias to "local" (the profile's OLD_ALIASES), once, and
# removes the search settings from Open WebUI once the search gateway has them (~/.config/localllm/search-gateway.json).
# It also sets document search for every chat: a multilingual embedding model and hybrid search (open-webui/sync.py).
# Then install/chat-types.sh builds the protected chats from ~/.config/localllm/chat-types/.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPEN_WEBUI_DIR="${SCRIPT_DIR}/open-webui"
CONFIG_DIR="${HOME}/.config/localllm"
WEBUI_URL="${WEBUI_URL:-http://localhost:3000}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log() { echo "[webui-config] $*"; }

api_post() {
  local endpoint="$1"
  shift
  curl -s -X POST "${WEBUI_URL}/${endpoint}" \
    -H "Content-Type: application/json" "$@"
}

# ---------------------------------------------------------------------------
# Deploy tools and functions from open-webui/
# ---------------------------------------------------------------------------

deploy_tools() {
  log "Deploying tools from ${OPEN_WEBUI_DIR}/tools/"
  for tool in "${OPEN_WEBUI_DIR}"/tools/*.py; do
    [ -f "$tool" ] || continue
    local name
    name="$(basename "$tool" .py)"
    log "  Tool: ${name}"
    api_post "api/tools/${name}" \
      -d "$(cat "$tool")"
  done
}

deploy_functions() {
  log "Deploying functions from ${OPEN_WEBUI_DIR}/functions/"
  for func in "${OPEN_WEBUI_DIR}"/functions/*.py; do
    [ -f "$func" ] || continue
    local name
    name="$(basename "$func" .py)"
    log "  Function: ${name}"
    api_post "api/functions/${name}" \
      -d "$(cat "$func")"
  done
}

# ---------------------------------------------------------------------------
# Migrate old alias → "local" (once per profile)
# ---------------------------------------------------------------------------

migrate_alias() {
  local profile_name="$1"
  local aliases_file="${CONFIG_DIR}/profiles/${profile_name}.json"

  [ -f "$aliases_file" ] || { log "No profile ${profile_name}, skipping alias migration"; return; }

  local old_aliases
  old_aliases="$(python3 -c "import json,sys; print(' '.join(json.load(open('${aliases_file}')).get('OLD_ALIASES', [])))")"

  [ -z "$old_aliases" ] && { log "No OLD_ALIASES for ${profile_name}, nothing to migrate"; return; }

  log "Migrating old aliases for ${profile_name}: ${old_aliases}"
  for old in $old_aliases; do
    log "  Renaming '${old}' → 'local'"
    api_post "api/models/${old}/rename" -d "{\"name\":\"local\"}"
  done

  # Clear OLD_ALIASES so we don't re-run
  python3 -c "
import json, sys
with open('${aliases_file}', 'r') as f:
    data = json.load(f)
data['OLD_ALIASES'] = []
with open('${aliases_file}', 'w') as f:
    json.dump(data, f, indent=2)
"
  log "Alias migration complete for ${profile_name}"
}

# ---------------------------------------------------------------------------
# Remove legacy search settings (hand off to search-gateway)
# ---------------------------------------------------------------------------

cleanup_search_settings() {
  local gateway_config="${CONFIG_DIR}/search-gateway.json"

  [ -f "$gateway_config" ] || { log "No search-gateway config at ${gateway_config}, skipping"; return; }

  log "Removing legacy Open WebUI search settings (search gateway handles this now)"
  api_post "api/settings/search" -d '{"enabled":false}'
}

# ---------------------------------------------------------------------------
# Sync document search for every chat (multilingual embedding + hybrid)
# ---------------------------------------------------------------------------

sync_document_search() {
  log "Configuring document search for all chats"

  if [ -f "${OPEN_WEBUI_DIR}/sync.py" ]; then
    python3 "${OPEN_WEBUI_DIR}/sync.py"
    log "Document search synced via sync.py"
  else
    log "No sync.py found at ${OPEN_WEBUI_DIR}/sync.py, skipping document search sync"
  fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
  local profile="${1:-Qwen-14B-Chat}"

  log "Starting webui-config for profile: ${profile}"

  deploy_tools
  deploy_functions
  migrate_alias "$profile"
  cleanup_search_settings
  sync_document_search

  log "Done."
}

main "$@"
