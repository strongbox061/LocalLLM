#!/usr/bin/env bash
# Starts the Kokoro text-to-speech server and the voice router in front of it (which picks the voice from the
# text's language), and waits until both are ready.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

# In split mode it runs on the helper PC; when owui is not in LOCALLLM_APPS it stays off.
apps_has owui || { info "owui not enabled, skipping Kokoro TTS"; exit 0; }

KOKORO_PORT="${KOKORO_PORT:-8088}"
ROUTER_PORT="${ROUTER_PORT:-8089}"
KOKORO_HOST="${KOKORO_HOST:-localhost}"

info "Starting Kokoro TTS server on ${KOKORO_HOST}:${KOKORO_PORT}..."
python3 -m kokoro_tts --port "$KOKORO_PORT" --host "$KOKORO_HOST" &
KOKORO_PID=$!

info "Starting voice router on port ${ROUTER_PORT}..."
python3 -m voice_router --port "$ROUTER_PORT" --backend "http://${KOKORO_HOST}:${KOKORO_PORT}" &
ROUTER_PID=$!

# Wait for both services to be ready
wait_for_service "$KOKORO_HOST" "$KOKORO_PORT" || { error "Kokoro TTS failed to start"; kill "$KOKORO_PID" 2>/dev/null; exit 1; }
wait_for_service localhost "$ROUTER_PORT" || { error "Voice router failed to start"; kill "$KOKORO_PID" "$ROUTER_PID" 2>/dev/null; exit 1; }

info "Kokoro TTS and voice router are ready"

# Trap to clean up on exit
cleanup() {
    info "Shutting down Kokoro TTS services..."
    kill "$KOKORO_PID" "$ROUTER_PID" 2>/dev/null || true
    wait "$KOKORO_PID" "$ROUTER_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

wait
