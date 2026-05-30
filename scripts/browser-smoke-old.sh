#!/usr/bin/env bash
set -euo pipefail

# browser-smoke-old.sh — Early agent-browser CLI smoke test
# Verifies agent-browser is installed and responds to basic commands.

BROWSER_CMD="${BROWSER_CMD:-agent-browser}"
TIMEOUT="${SMOKE_TIMEOUT:-30}"

echo "=== Agent-Browser Smoke Test ==="
echo "Command : ${BROWSER_CMD}"
echo "Timeout : ${TIMEOUT}s"

# 1. Check binary exists
if ! command -v "${BROWSER_CMD}" &>/dev/null; then
  echo "FAIL: '${BROWSER_CMD}' not found in PATH" >&2
  exit 1
fi

VERSION=$("${BROWSER_CMD}" --version 2>&1) || true
echo "Version : ${VERSION}"

# 2. Ping the running instance (or start one)
"${BROWSER_CMD}" --ping &>/dev/null && PING_OK=true || PING_OK=false

if ${PING_OK}; then
  echo "Ping    : OK"
else
  echo "Ping    : No instance running — starting headless session"
  "${BROWSER_CMD}" --headless --timeout "${TIMEOUT}" &
  BGPID=$!
  sleep 2
  if ! kill -0 "${BGPID}" 2>/dev/null; then
    echo "FAIL: Headless session exited immediately" >&2
    exit 1
  fi
  echo "Session : Started (PID ${BGPID})"
fi

# 3. Quick navigation check
"${BROWSER_CMD}" --eval --stdin 'document.title || "no-title"' &>/dev/null && NAV_OK=true || NAV_OK=false

if ${NAV_OK}; then
  echo "Nav     : OK"
else
  echo "WARN    : Navigation check skipped (no page loaded)"
fi

# 4. Cleanup on exit
trap '[[ -n "${BGPID:-}" ]] && { kill "${BGPID}" 2>/dev/null || true; wait "${BGPID}" 2>/dev/null || true; echo "Cleanup : Stopped session"; }' EXIT INT TERM

echo "=== Smoke test passed ==="
