#!/usr/bin/env bash
# Builds llama.cpp with CUDA, downloads the active model profile's model and vision projector (models/<name>.env,
# MODEL_PROFILE in local.env), and installs the localllm-llm service (not enabled).
#   scripts/install-llama.sh      only what is missing
#   scripts/install-llama.sh --update  also pull and rebuild llama.cpp
#   A fresh clone checks out the llama.cpp commit recorded by the last backup (versions.env), if any.
# Before downloading the model, it checks the files' sizes on Hugging Face against the free space.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"
source "$REPO_DIR/lib/model.sh"
load_model_profile || exit 1
echo "Model profile: $MODEL_PROFILE ($MODEL_NAME)"

LLAMA_DIR="$LLM_HOME/llama.cpp"
MODELS_DIR="$MODELS_HOME"

# --- Configuration ---
UPDATE=false
if [[ "${1:-}" == "--update" ]]; then
    UPDATE=true
fi

# cuda.sh uses append_path, which /etc/profile defines and then removes again.
append_path() {
    case ":$PATH:" in
        *:"$1":*) ;;
        *) PATH="${PATH:+$PATH:}$1" ;;
    esac
}
set +u
source /etc/profile.d/cuda.sh
set -u
unset -f append_path

# --- llama.cpp ---
# The GPU generation to build for: LLAMA_CUDA_ARCH in local.env (e.g. 89 for RTX 40), else read from the GPU itself.
CUDA_ARCH="${LLAMA_CUDA_ARCH:-$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -1 | tr -d ' .')}"

# A build for another GPU must be redone: rebuild when the architecture differs from the last build's.
BUILT_ARCH="$(sed -nE 's/^CMAKE_CUDA_ARCHITECTURES:[A-Z]+=(.*)$/\1/p' "$LLAMA_DIR/build/CMakeCache.txt" 2>/dev/null)"

# --- model and vision projector ---
hf_download() {
    # hf_download <file in MODEL_REPO>, into MODELS_DIR
    local file="$1"
    local url="https://huggingface.co/$MODEL_REPO/resolve/main/$file"
    local dest="$MODELS_DIR/$file"

    if [[ -f "$dest" ]]; then
        info "Skipping download of $file (already exists)"
        return 0
    fi

    local size
    size="$(hf_size "$file")"
    if [[ -z "$size" ]]; then
        error "Could not determine size of $file on Hugging Face"
        return 1
    fi

    # Check free space
    local avail
    avail="$(df --output=avail "$MODELS_DIR" | tail -1)"
    avail=$((avail * 1024))

    if (( size > avail )); then
        error "Not enough free space in $MODELS_DIR: need $size bytes, have $avail bytes"
        return 1
    fi

    info "Downloading $file ($(numfmt --to=iec "$size") from Hugging Face)"
    curl -L --progress-bar "$url" -o "$dest"
    info "Downloaded $file"
}

hf_size() {
    # hf_size <file in MODEL_REPO>: its size in bytes, or nothing
    local file="$1"
    local url="https://huggingface.co/$MODEL_REPO/resolve/main/$file"
    curl -sI "$url" | grep -i '^content-length:' | awk '{print $2}' | tr -d '\r'
}

# --- Build llama.cpp ---
build_llama() {
    info "Building llama.cpp with CUDA architecture ${CUDA_ARCH:-unknown}"

    if [[ ! -d "$LLAMA_DIR" ]]; then
        info "Cloning llama.cpp into $LLAMA_DIR"
        git clone https://github.com/ggerganov/llama.cpp.git "$LLAMA_DIR"
    elif $UPDATE; then
        info "Pulling latest llama.cpp changes"
        cd "$LLAMA_DIR" && git pull || true
        cd - > /dev/null
    fi

    if [[ ! -x "$LLAMA_DIR/build/bin/llama-server" ]] || [[ "$BUILT_ARCH" != "$CUDA_ARCH" ]]; then
        cd "$LLAMA_DIR"
        echo "Building llama.cpp for CUDA architecture $CUDA_ARCH."
        cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES="$CUDA_ARCH" -DCMAKE_BUILD_TYPE=Release
        cmake --build build -j"$(nproc)" --target llama-server llama-bench
        cd "$REPO_DIR"
    else
        info "llama.cpp already built for architecture $BUILT_ARCH"
    fi

    append_path "$LLAMA_DIR/build/bin"
}

# --- Download model and vision projector ---
download_model() {
    if [[ -z "${MODEL_FILE:-}" ]]; then
        error "No MODEL_FILE set in profile"
        return 1
    fi

    # Download model file
    hf_download "$MODEL_FILE"

    # Download vision projector if present
    if [[ -n "${MMPROJ_FILE:-}" ]]; then
        hf_download "$MMPROJ_FILE"
    fi
}

# --- Service setup ---
install_service() {
    local UNIT="/etc/systemd/system/localllm-llm.service"
    local RENDERED
    RENDERED="$(sed "s|@USER@|$(id -un)|g; s|@REPO_DIR@|$REPO_DIR|g" "$REPO_DIR/services/localllm-llm.service")"

    echo "$RENDERED" | sudo tee "$UNIT" > /dev/null
    sudo systemctl daemon-reload
    info "Service installed at $UNIT (not enabled; start with ./start.sh)"
}

# --- Main ---
main() {
    build_llama
    download_model
    install_service
    info "llama.cpp setup complete"
}

main "$@"
