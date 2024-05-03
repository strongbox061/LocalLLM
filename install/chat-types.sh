#!/usr/bin/env bash
# Builds Open WebUI's protected chats from the chat type files in ~/.config/localllm/chat-types/*.json (README,
# "Protected chats in Open WebUI"): one preset per file, with only the Protected Lookup tool, the Protected Chat filter
# and the file's allowed sites, prompt, starter prompts, users and Knowledge base. Run it after
# install/open-webui-config.sh, and again after editing a chat type. A preset edited by hand in Open WebUI is
# overwritten: the files are the source of truth.
#   install/chat-types.sh                    create or update the presets
#   install/chat-types.sh --refresh-sources  also download the files' official sources into their Knowledge bases
#   install/chat-types.sh --dry-run [dir]    check the files (or those in dir) and print the presets; change nothing
#   install/chat-types.sh [--refresh-sources] --only <id>   only that chat type
#   install/chat-types.sh --remove <id>      delete its preset and Knowledge base from Open WebUI (not the file)
# The page http://127.0.0.1:8090/chat-types does all of this from the browser (README, "Protected chats").
# In split mode (Open WebUI on the helper PC; README, "Two-PC setup") the files stay here: this sends them to the helper
# (with any other changed helper files, as ./localllm.sh deploy does, without its guard checks) and builds there.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

USAGE="Usage: $0 [--refresh-sources] [--only <id>] | --dry-run [dir] | --remove <id>"
OPTS=()
case "${1:-}" in
    "") ;;
    --refresh-sources | --only)
        [[ "$1" == "--refresh-sources" ]] && { OPTS=(--refresh-sources); shift; }
        if [[ $# -gt 0 ]]; then
            [[ "$1" == "--only" && $# -eq 2 ]] || die "$USAGE"
            OPTS+=(--only "$2")
        fi ;;
    --dry-run) OPTS=(--dry-run); CHAT_TYPES_DIR="${2:-$CHAT_TYPES_DIR}" ;;
    --remove) [[ $# -eq 2 ]] || die "$USAGE"; OPTS=(--remove "$2") ;;
    *) die "$USAGE" ;;
esac

if [[ "${OPTS[0]:-}" != "--remove" ]] && { [[ ! -d "$CHAT_TYPES_DIR" ]] || ! compgen -G "$CHAT_TYPES_DIR/*.json" >/dev/null; }; then
    echo "No chat types in $CHAT_TYPES_DIR: nothing to do."
    echo "To make one: mkdir -p $CHAT_TYPES_DIR && cp $REPO_DIR/open-webui/chat-types/example.json $CHAT_TYPES_DIR/<name>.json"
    exit 0
fi

if [[ "${OPTS[0]:-}" != "--dry-run" ]] && ! apps_has owui; then
    die "Open WebUI is off (LOCALLLM_APPS=$LOCALLLM_APPS): ./localllm.sh apps pi,owui turns it back on."
fi

if [[ "${WHERE_OPEN_WEBUI-main}" != main && "${OPTS[0]:-}" != "--dry-run" ]]; then
    # shellcheck source=lib/helper.sh
    source "$REPO_DIR/lib/helper.sh"
    require_helper_connection
    if [[ "${OPTS[0]:-}" != "--remove" ]]; then
        deploy_files --no-checks
        kept=()
        for f in "${DEPLOYED[@]}"; do [[ "$f" == chat-types/* ]] || kept+=("$f"); done
        DEPLOYED=("${kept[@]}")
        apply_deployed
    fi
    log "Chat types on the helper PC"
    helper_task chat-types "${OPTS[@]}"
    exit $?
fi

source "$REPO_DIR/lib/model.sh"
load_model_profile || exit 1
PY="$WEBUI_PYTHON"
if [[ "${OPTS[0]:-}" == "--dry-run" ]]; then
    [[ -x "$PY" ]] || PY=python3
else
    [[ -x "$WEBUI_PYTHON" ]] || die "Open WebUI is not installed: run install/open-webui.sh first."
    curl -fsS -m 3 -o /dev/null "$WEBUI_URL/health" 2>/dev/null || die "Open WebUI is not running: run start/open-webui.sh first."
    [[ -f "$WEBUI_KEY_FILE" ]] || die "No $WEBUI_KEY_FILE (Open WebUI creates it on its first start)."
    write_model_json >/dev/null
fi
"$PY" -u "$REPO_DIR/open-webui/chat_types.py" "$WEBUI_URL" "$WEBUI_DATA_DIR/webui.db" "$WEBUI_KEY_FILE" \
    "$MODEL_JSON_FILE" "$CHAT_TYPES_DIR" "${OPTS[@]}"

# --- Helpers ---
log() { echo "[chat-types] $*"; }
die() { echo "Error: $*" >&2; exit 1; }

api_get() {
    local path="$1"
    curl -sf -H "Authorization: Bearer $API_KEY" "$OPEN_WEBUI_URL/api/$path"
}

api_post() {
    local path="$1"
    local data="$2"
    curl -sf -X POST -H "Authorization: Bearer $API_KEY" \
        -H "Content-Type: application/json" \
        -d "$data" "$OPEN_WEBUI_URL/api/$path"
}

# --- Load a chat type JSON ---
load_chat_type() {
    local file="$1"
    [[ -f "$file" ]] || die "Chat type file not found: $file"

    # Extract fields using jq (minimal, no error handling for brevity)
    CT_ID=$(jq -r '.id // empty' "$file")
    CT_NAME=$(jq -r '.name // "Unnamed"' "$file")
    CT_PROMPT=$(jq -r '.prompt // ""' "$file")
    CT_USERS=$(jq -r '.users // [] | join(",")' "$file")
    CT_SITES=$(jq -r '.allowed_sites // [] | join(",")' "$file")
    CT_KB_ID=$(jq -r '.knowledge_base_id // ""' "$file")
    CT_STarter_PROMPTS=$(jq -r '.starter_prompts // [] | join("\n")' "$file")

    [[ -n "$CT_ID" ]] || die "Chat type file missing 'id': $file"
}

# --- Build a preset for Open WebUI ---
build_preset() {
    local file="$1"
    load_chat_type "$file"

    local tools='[{"type": "function", "function": {"name": "protected_lookup", "description": "Protected knowledge lookup"}}]'
    local filters="[\"protected\"]"
    local kb_ids="[]"
    [[ -n "$CT_KB_ID" ]] && kb_ids="[\"$CT_KB_ID\"]"

    local preset
    preset=$(jq -n \
        --arg id "preset-$CT_ID" \
        --arg name "Protected: $CT_NAME" \
        --arg prompt "$CT_PROMPT" \
        --argjson tools "$tools" \
        --argjson filters "$filters" \
        --argjson kb_ids "$kb_ids" \
        --arg model "$DEFAULT_MODEL" \
        --argjson users "$(jq -r '.users // []' "$file")" \
        --argjson starter_prompts "[$(echo "$CT_STARTER_PROMPTS" | sed '/^$/d' | sed 's/.*$/"&"/' | paste -sd,)]" \
        '{
            id: $id,
            name: $name,
            prompt: $prompt,
            tools: $tools,
            filters: $filters,
            knowledge_base_ids: $kb_ids,
            model: $model,
            users: $users,
            starter_prompts: $starter_prompts,
            is_preset: true
        }')

    echo "$preset"
}

# --- Create or update a preset in Open WebUI ---
apply_preset() {
    local file="$1"
    local preset
    preset=$(build_preset "$file")

    if $DRY_RUN; then
        log "DRY-RUN: would create/update preset for $(echo "$preset" | jq -r '.name')"
        echo "$preset" | jq .
        return
    fi

    # Check if preset already exists
    local existing
    existing=$(api_get "community/presets" || true)
    local preset_id
    preset_id=$(echo "$preset" | jq -r '.id')

    log "Applying preset: $(echo "$preset" | jq -r '.name')"
    api_post "community/presets" "$preset" > /dev/null
    log "Done."
}

# --- Remove a preset ---
remove_preset() {
    local id="$1"
    local preset_id="preset-$id"

    if $DRY_RUN; then
        log "DRY-RUN: would remove preset $preset_id"
        return
    fi

    log "Removing preset: $preset_id"
    api_post "community/presets/$preset_id/delete" '{}' > /dev/null 2>&1 || true
    log "Done."
}

# --- Refresh sources into Knowledge base ---
refresh_sources() {
    local file="$1"
    load_chat_type "$file"

    [[ -n "$CT_KB_ID" ]] || { log "No knowledge base for $CT_ID, skipping."; return; }

    local sources
    sources=$(jq -r '.sources // [] | .[]' "$file")
    [[ -n "$sources" ]] || { log "No sources for $CT_ID, skipping."; return; }

    log "Refreshing sources for $CT_ID..."
    while IFS= read -r url; do
        log "  Downloading: $url"
        # Simple curl download — real implementation would handle retries, dedup, etc.
        local tmpfile
        tmpfile=$(mktemp)
        if curl -sfL -o "$tmpfile" "$url" 2>/dev/null; then
            log "  Saved to temp: $tmpfile"
            # In a real implementation, upload to the knowledge base here
        else
            log "  Failed to download: $url"
        fi
        rm -f "$tmpfile"
    done <<< "$sources"
}

# --- Main ---
main() {
    # Handle --remove
    if [[ -n "$REMOVE_ID" ]]; then
        remove_preset "$REMOVE_ID"
        return
    fi

    # Validate chat types directory
    [[ -d "$CHAT_TYPES_DIR" ]] || die "Chat types directory not found: $CHAT_TYPES_DIR"

    # Collect target files
    local files=()
    for f in "$CHAT_TYPES_DIR"/*.json; do
        [[ -f "$f" ]] || continue
        local id
        id=$(jq -r '.id // empty' "$f" 2>/dev/null)
        if [[ -n "$ONLY_ID" && "$id" != "$ONLY_ID" ]]; then
            continue
        fi
        files+=("$f")
    done

    if [[ ${#files[@]} -eq 0 ]]; then
        log "No chat type files found${ONLY:+ (only=$ONLY_ID)}."
        return
    fi

    # Process each file
    for file in "${files[@]}"; do
        apply_preset "$file"
        $REFRESH_SOURCES && refresh_sources "$file"
    done

    log "Processed ${#files[@]} chat type(s)."
}

main
