#!/usr/bin/env bash
# Allows autoplay in a Chromium-based browser for Open WebUI's address, whatever it is now: http://127.0.0.1:3000 in
# single mode, https://<the helper's Tailscale name> in split mode (WEBUI_URL from the generated endpoints, README
# "Two-PC setup").
# Voice mode's answers play through open-webui/loader.js (served by Open WebUI itself); when a browser still refuses a
# sentence, loader.js plays it through a fresh audio object, which Chromium-based browsers allow on sites in this policy
# (${BROWSER_POLICY_DIR}/managed/localllm-autoplay.json).
# 127.0.0.1:3000 and localhost:3000 stay in it, so going back to single mode needs nothing. Run by install.sh and
# ./localllm.sh switch; no-op when BROWSER_POLICY_DIR is unset in local.env. Safe to run again.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

# A managed policy: AutoplayAllowlist can't be a recommended one.
if [[ -z "${BROWSER_POLICY_DIR:-}" ]]; then
    echo "BROWSER_POLICY_DIR is unset in local.env; skipping browser autoplay policy."
    exit 0
fi

ORIGINS=("http://127.0.0.1:3000" "http://localhost:3000")
url="${WEBUI_URL%/}"
[[ " ${ORIGINS[*]} " == *" $url "* ]] || ORIGINS+=("$url")

policy="$(python3 -c 'import json,sys; print(json.dumps({"AutoplayAllowlist": sys.argv[1:]}))' "${ORIGINS[@]}")"
if install_system_file "${BROWSER_POLICY_DIR%/}/managed/localllm-autoplay.json" "$policy"; then
    echo "Browser allows autoplay on: ${ORIGINS[*]} (restart your browser to apply)."
fi
