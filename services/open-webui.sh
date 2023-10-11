#!/usr/bin/env bash

# Open WebUI writes .webui_secret_key into the current directory: always use backup/open-webui,
# so the key is saved with the rest of the backup (backup/ is not in git; see archive-backup.sh).
KEY_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/backup/open-webui"
mkdir -p "$KEY_DIR"
cd -- "$KEY_DIR" || { echo "ERROR: cannot enter $KEY_DIR" >&2; exit 1; }

export DATA_DIR="$HOME/.open-webui"

# --- Configuration ---
# Only used on a brand-new install. Afterwards the settings saved in Open WebUI win.
export WEBUI_SECRET_KEY="$(cat .webui_secret_key 2>/dev/null || head -c 32 /dev/urandom | base64 | tee .webui_secret_key)"

# Voice: Whisper "small" on the CPU for dictation (services/voice_router.py),
# which picks the voice from the language when the voice is "auto".
export WHISPER_MODEL="small"
export WHISPER_LANG="auto"

# Stay under llama-server's slot (the model profile's CTX_PER_SLOT, less room for the answer):
# compact a chat at 7/8 of what is left, 50176 tokens with the default profile's 64K slot.
export OLLAMA_MAX_TOKENS=50176

# No background requests that would disturb the cache
export WEBUI_JWT_SECRET_KEY="$(cat .webui_secret_key)"

# --- Source helpers ---
# shellcheck source=../lib/common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh" && pwd)"

# shellcheck source=../lib/model.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../lib/model.sh" && pwd)"

# --- Voice loader for browser compatibility ---
# Voice fixes for every browser, served as Open WebUI's /static/loader.js
# Voice mode still works without them, so a failure here doesn't stop Open WebUI.
cat > "$KEY_DIR/loader.js" 2>/dev/null <<'LOADER_EOF' || true
// Open WebUI voice loader — browser polyfills for MediaRecorder and AudioContext
(function() {
  if (!window.MediaRecorder) {
    window.MediaRecorder = function(stream) {
      var chunks = [];
      return {
        start: function() { stream.ondataavailable = function(e) { chunks.push(e.data); }; },
        stop: function(cb) {
          stream.ondataavailable = null;
          setTimeout(function() { cb(new Blob(chunks, { type: 'audio/webm' })); }, 100);
        }
      };
    };
  }
  if (!window.AudioContext) {
    window.AudioContext = window.webkitAudioContext || function() {};
  }
})();
LOADER_EOF

echo "Open WebUI configured. Key stored in $KEY_DIR"
