#!/usr/bin/env bash
# Allows autoplay in a Chromium-based browser for Open WebUI's address, whatever it is now: http://127.0.0.1:3000 in
# single mode, https://<the helper's Tailscale name> in split mode (WEBUI_URL from the generated endpoints, README
# "Two-PC setup").

set -euo pipefail

# Voice mode's answers play through open-webui/loader.js (served by Open WebUI itself); when a browser still refuses a
# sentence, loader.js plays it through a fresh audio object, which Chromium-based browsers allow on sites in this policy
# (${BROWSER_POLICY_DIR}/managed/localllm-autoplay.json).
# 127.0.0.1:3000 and localhost:3000 stay in it, so going back to single mode needs nothing. Run by install.sh and
# ./localllm.sh switch; no-op when BROWSER_POLICY_DIR is unset in local.env. Safe to run again.

ORIGINS=("http://127.0.0.1:3000" "http://localhost:3000")

# A managed policy: AutoplayAllowlist can't be a recommended one.
BROWSER_POLICY_DIR="${BROWSER_POLICY_DIR:-}"

if [[ -z "$BROWSER_POLICY_DIR" ]]; then
  echo "BROWSER_POLICY_DIR not set — skipping browser autoplay policy."
  exit 0
fi

POLICY_FILE="${BROWSER_POLICY_DIR}/managed/localllm-autoplay.json"

# Build the JSON allowlist
ORIGINS_JSON="["
for i in "${!ORIGINS[@]}"; do
  [[ $i -gt 0 ]] && ORIGINS_JSON+=","
  ORIGINS_JSON+="\"${ORIGINS[$i]}\""
done
ORIGINS_JSON+="]"

cat > "$POLICY_FILE" <<EOF
{
  "AutoplayAllowlist": [${ORIGINS_JSON}]
}
EOF

echo "Wrote browser autoplay policy to $POLICY_FILE"
