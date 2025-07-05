#!/usr/bin/env bash
# Starts Open WebUI and waits until it answers.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"
# In split mode it runs on the helper PC; when owui is not in LOCALLLM_APPS it stays off.
apps_has owui \
    || die "Open WebUI is off (LOCALLLM_APPS=$LOCALLLM_APPS): ./localllm.sh apps pi,owui turns it back on."
[[ "${WHERE_OPEN_WEBUI-main}" == main ]] \
    || die "Open WebUI runs on the helper PC in split mode: ./localllm.sh start open-webui (or switch single first)."

log "Open WebUI"
if curl -fsS -m 2 -o /dev/null "$WEBUI_URL/health" 2>/dev/null; then
    echo "Already running."
else
    # It may already be starting (e.g. right after a restart): only start it when its process isn't there.
    webui_running || systemctl --user start open-webui
    echo "Starting..."
    wait_for "$WEBUI_URL/health" 180 || die "Open WebUI did not become ready. Logs: journalctl --user -u open-webui -e"
fi
# Its data came from a backup made in split mode (restore.sh): its settings still name the helper's services. Set
# them for this PC, once (the Knowledge bases are indexed again with this PC's embedding model: a few minutes).
if [[ -f "$WEBUI_FROM_SPLIT_FILE" ]]; then
    log "Open WebUI's settings for this PC (its data came from a backup made in split mode)"
    if webui_settings_single; then
        rm -f "$WEBUI_FROM_SPLIT_FILE"
        echo "Open WebUI is set for this PC."
    else
        warn "Not every setting was set for this PC (see above): the next start/open-webui.sh tries again."
    fi
fi
echo "Open WebUI: $WEBUI_URL"
