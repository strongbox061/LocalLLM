#!/usr/bin/env bash
# Starts the Kokoro text-to-speech server and the voice router in front of it (which picks the voice from the
# text's language), and waits until both are ready.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"
# In split mode it runs on the helper PC; when owui is not in LOCALLLM_APPS it stays off.
apps_has owui \
    || die "Kokoro is off (LOCALLLM_APPS=$LOCALLLM_APPS): ./localllm.sh apps pi,owui turns it back on."
[[ "${WHERE_KOKORO-main}" == main ]] \
    || die "Kokoro runs on the helper PC in split mode: ./localllm.sh start kokoro (or switch single first)."

log "Kokoro (text-to-speech)"
if curl -fsS -m 2 -o /dev/null "$KOKORO_URL/health" 2>/dev/null; then
    echo "Already running."
else
    kokoro_compose up -d
    echo "Starting..."
    wait_for "$KOKORO_URL/health" 90 || die "Kokoro did not become ready. Logs: docker logs kokoro"
fi

if curl -fsS -m 2 -o /dev/null "$VOICE_ROUTER_URL/health" 2>/dev/null; then
    echo "Voice router already running."
else
    systemctl --user start voice-router
    wait_for "$VOICE_ROUTER_URL/health" 30 \
        || die "The voice router did not become ready. Logs: journalctl --user -u voice-router -e"
fi
echo "Kokoro: $KOKORO_URL (Open WebUI uses it through the voice router: $VOICE_ROUTER_URL)"
