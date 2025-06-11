#!/usr/bin/env bash
# Downloads the Kokoro text-to-speech server image (CPU only, about 5 GB on disk) and installs the voice-router
# user service, which picks the voice from the text's language. Start both with start/kokoro.sh.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

image="$(kokoro_compose config --images)"
if docker_cmd image inspect "$image" >/dev/null 2>&1; then
    echo "Kokoro image already downloaded: $image"
else
    kokoro_compose pull
fi

[[ -x "$WEBUI_PYTHON" ]] || die "Open WebUI's Python is missing (the voice router uses it): run install/open-webui.sh first."
install_user_unit voice-router.service \
    "$(sed "s|@PYTHON@|$WEBUI_PYTHON|; s|@REPO_DIR@|$REPO_DIR|" "$REPO_DIR/services/voice-router.service")"
