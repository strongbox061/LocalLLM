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
        # The other sent files take effect as after a deploy; the chat types are built just below, as asked.
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
    [[ -x "$PY" ]] || PY=python3   # writes nothing: model.json is only read (missing: the alias "local")
else
    [[ -x "$WEBUI_PYTHON" ]] || die "Open WebUI is not installed: run install/open-webui.sh first."
    curl -fsS -m 3 -o /dev/null "$WEBUI_URL/health" 2>/dev/null || die "Open WebUI is not running: run start/open-webui.sh first."
    [[ -f "$WEBUI_KEY_FILE" ]] || die "No $WEBUI_KEY_FILE (Open WebUI creates it on its first start)."
    write_model_json >/dev/null
fi
"$PY" -u "$REPO_DIR/open-webui/chat_types.py" "$WEBUI_URL" "$WEBUI_DATA_DIR/webui.db" "$WEBUI_KEY_FILE" \
    "$MODEL_JSON_FILE" "$CHAT_TYPES_DIR" "${OPTS[@]}"
