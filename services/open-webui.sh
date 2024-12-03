#!/usr/bin/env bash

# Open WebUI writes .webui_secret_key into the current directory: always use backup/open-webui,
# so the key is saved with the rest of the backup (backup/ is not in git; see archive-backup.sh).
KEY_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/backup/open-webui"
mkdir -p "$KEY_DIR"
cd -- "$KEY_DIR" || { echo "ERROR: cannot enter $KEY_DIR" >&2; exit 1; }

export DATA_DIR="$HOME/.open-webui"

# What Open WebUI writes (its signing key, its database with your chats, uploads) is only yours, also files it makes
# later; files from before this rule are fixed here (lib/common.sh make_private).
umask 077
# shellcheck source=../lib/common.sh
source "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")/../lib/common.sh"
make_private

# Only used on a brand-new install. Afterwards the settings saved in Open WebUI win.
export OPENAI_API_BASE_URL="http://127.0.0.1:8081/v1"
export OPENAI_API_KEY="none"

# Voice, also only for a brand-new install: Whisper "small" on the CPU for dictation,
# (services/voice_router.py), which picks the voice from the language when the voice is "auto".
export WHISPER_MODEL=small
# Always applied (not saved in Open WebUI's settings): skip silence and noise before transcribing, so a clip with
# no speech comes back empty instead of as made-up text ("Thank you", repeated characters, a wrong language).
export WHISPER_VAD_FILTER=true
export AUDIO_TTS_ENGINE=openai
export AUDIO_TTS_OPENAI_API_BASE_URL="http://127.0.0.1:8881/v1"
export AUDIO_TTS_OPENAI_API_KEY="not-needed"
export AUDIO_TTS_VOICE=auto

# Stay under llama-server's slot (the model profile's CTX_PER_SLOT, less room for the answer): compact a chat at 7/8
# of what is left, 50176 tokens with the default profile's 64K slot.
# shellcheck source=../lib/model.sh
source "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")/../lib/model.sh"
load_model_profile || exit 1
export ENABLE_CONTEXT_COMPACTION=true
export CONTEXT_COMPACTION_TOKEN_THRESHOLD=$(( (CTX_PER_SLOT - MAX_TOKENS) * 7 / 8 ))

# No background requests that would disturb the cache
export ENABLE_TITLE_GENERATION=false
export ENABLE_TAGS_GENERATION=false
export ENABLE_FOLLOW_UP_GENERATION=false
export ENABLE_AUTOCOMPLETE_GENERATION=false

export WEB_LOADER_ENGINE=playwright
