#!/usr/bin/env bash
# Builds llama.cpp with CUDA, downloads the active model profile's model and vision projector (models/<name>.env,
# MODEL_PROFILE in local.env), and installs the localllm-llm service (not enabled).
#   install/llama.sh            only what is missing
#   install/llama.sh --update   also pull and rebuild llama.cpp
#   A fresh clone checks out the llama.cpp commit recorded by the last backup (versions.env), if any.
# Before downloading the model, it checks the files' sizes on Hugging Face against the free space.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"
source "$REPO_DIR/lib/model.sh"
load_model_profile || exit 1
echo "Model profile: $MODEL_PROFILE ($MODEL_NAME)"

LLAMA_DIR="$LLM_HOME/llama.cpp"
# MODEL_REPO, MODEL_FILE and the vision projector (MMPROJ_FILE, saved as MMPROJ_LOCAL) come from the profile.
MODELS_DIR="$MODELS_HOME"

UPDATE=false
[[ "${1:-}" == "--update" ]] && UPDATE=true

pacman -Q base-devel cmake git cuda uv >/dev/null 2>&1 || sudo pacman -S --needed base-devel cmake git cuda uv
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
if [[ ! -d "$LLAMA_DIR/.git" ]]; then
    mkdir -p "$LLM_HOME"
    git clone https://github.com/ggml-org/llama.cpp "$LLAMA_DIR"
    # The commit of the last backup (versions.env), unless --update asks for the newest.
    if ! $UPDATE && [[ -n "${LLAMA_CPP_COMMIT:-}" ]]; then
        git -C "$LLAMA_DIR" checkout -q "$LLAMA_CPP_COMMIT"
        echo "llama.cpp: the recorded commit ${LLAMA_CPP_COMMIT:0:9}"
    fi
elif $UPDATE; then
    # Back on the default branch first (a fresh install may sit on the recorded commit).
    git -C "$LLAMA_DIR" checkout -q "$(git -C "$LLAMA_DIR" symbolic-ref --short refs/remotes/origin/HEAD | sed 's|^origin/||')"
    git -C "$LLAMA_DIR" pull --ff-only
fi

# The GPU generation to build for: LLAMA_CUDA_ARCH in local.env (e.g. 89 for RTX 40), else read from the GPU itself.
CUDA_ARCH="${LLAMA_CUDA_ARCH:-$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -1 | tr -d ' .')}"
[[ "$CUDA_ARCH" =~ ^[0-9]+$ ]] \
    || die "Can't tell the GPU's CUDA architecture: set LLAMA_CUDA_ARCH in $LOCAL_ENV_FILE (see config/local.example.env)."
# A build for another GPU must be redone: rebuild when the architecture differs from the last build's.
BUILT_ARCH="$(sed -nE 's/^CMAKE_CUDA_ARCHITECTURES:[A-Z]+=(.*)$/\1/p' "$LLAMA_DIR/build/CMakeCache.txt" 2>/dev/null)"
if $UPDATE || [[ ! -x "$LLAMA_DIR/build/bin/llama-server" ]] || [[ "$BUILT_ARCH" != "$CUDA_ARCH" ]]; then
    cd "$LLAMA_DIR"
    echo "Building llama.cpp for CUDA architecture $CUDA_ARCH."
    # A fresh install builds the commit of the last backup (versions.env) with the newest CUDA and compiler from
    # pacman, which an older llama.cpp may not build with.
    BUILD_HINT="Building llama.cpp failed."
    $UPDATE || [[ -z "${LLAMA_CPP_COMMIT:-}" ]] || BUILD_HINT+=" It is at the commit recorded by the last backup, which this system's CUDA or compiler may be too new for: run install/llama.sh --update (the newest llama.cpp), then ./install.sh again."
    cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES="$CUDA_ARCH" -DCMAKE_BUILD_TYPE=Release || die "$BUILD_HINT"
    cmake --build build -j"$(nproc)" --target llama-server llama-bench || die "$BUILD_HINT"
    cd "$REPO_DIR"
else
    echo "llama-server is already built."
fi

# --- model and vision projector ---
HF=()
hf_download() {    # hf_download <file in MODEL_REPO>, into MODELS_DIR
    if [[ ${#HF[@]} -eq 0 ]]; then
        uv tool install "huggingface_hub[cli]" --with hf_transfer

        # Newer huggingface_hub versions only ship `hf`; older ones ship `huggingface-cli`.
        local hf_auth
        if command -v huggingface-cli >/dev/null; then
            HF=(huggingface-cli)
            hf_auth=(huggingface-cli)
        else
            HF=(hf)
            hf_auth=(hf auth)
        fi

        # The files are public; a Hugging Face login only makes downloads faster, so it's optional.
        "${hf_auth[@]}" whoami >/dev/null 2>&1 \
            || echo "Not logged in to Hugging Face: downloading anonymously (for faster downloads: ${hf_auth[*]} login)."
        mkdir -p "$MODELS_DIR"
    fi
    HF_HUB_ENABLE_HF_TRANSFER=1 "${HF[@]}" download "$MODEL_REPO" "$1" --local-dir "$MODELS_DIR"
}

# Room for what is still to download: the sizes come from Hugging Face, so this works for whatever model is chosen.
hf_size() {    # hf_size <file in MODEL_REPO>: its size in bytes, or nothing
    curl -fsSIL -m 20 "https://huggingface.co/$MODEL_REPO/resolve/main/$1" 2>/dev/null \
        | tr -d '\r' | sed -nE 's/^x-linked-size: *([0-9]+)$/\1/Ip' | tail -1
}
needed=0
[[ -f "$MODELS_DIR/$MODEL_FILE" ]] || needed=$((needed + $(hf_size "$MODEL_FILE" || echo 0)))
[[ -z "$MMPROJ_LOCAL" || -f "$MODELS_DIR/$MMPROJ_LOCAL" ]] || needed=$((needed + $(hf_size "$MMPROJ_FILE" || echo 0)))
if [[ $needed -gt 0 ]]; then
    mkdir -p "$MODELS_DIR"
    free=$(( $(df --output=avail -B1 "$MODELS_DIR" | tail -1) ))
    needed_gb=$(( (needed + 1073741823) / 1073741824 ))
    [[ $free -gt $((needed + 2 * 1073741824)) ]] \
        || die "The model needs ${needed_gb} GB (plus 2 GB to spare) in $MODELS_DIR, but only $((free / 1073741824)) GB are free."
    echo "Downloading ${needed_gb} GB of model files."
fi

if [[ -f "$MODELS_DIR/$MODEL_FILE" ]]; then
    echo "Model already downloaded: $MODELS_DIR/$MODEL_FILE"
else
    hf_download "$MODEL_FILE"
fi

if [[ -z "$MMPROJ_LOCAL" ]]; then
    echo "This model has no vision projector (no image input)."
elif [[ -f "$MODELS_DIR/$MMPROJ_LOCAL" ]]; then
    echo "Vision projector already downloaded: $MODELS_DIR/$MMPROJ_LOCAL"
else
    hf_download "$MMPROJ_FILE"
    mv "$MODELS_DIR/$MMPROJ_FILE" "$MODELS_DIR/$MMPROJ_LOCAL"
fi

# What thinking-switch.ts and the Open WebUI migration read (the service rewrites it at each start too).
write_model_json

# --- service: installed, deliberately not enabled (start it with ./start.sh) ---
# The service was called llama-qwen before: stop and remove it (start/llama.sh starts the new one).
OLD_UNIT="/etc/systemd/system/llama-qwen.service"
if [[ -f "$OLD_UNIT" ]]; then
    was_running=false
    systemctl is-active --quiet llama-qwen && was_running=true
    sudo systemctl disable --now llama-qwen 2>/dev/null || true
    sudo rm -f "$OLD_UNIT"
    sudo systemctl daemon-reload
    echo "Removed the old llama-qwen service."
    $was_running && echo "It was running: start the new one with start/llama.sh."
fi
UNIT="/etc/systemd/system/localllm-llm.service"
RENDERED="$(sed "s|@USER@|$(id -un)|g; s|@REPO_DIR@|$REPO_DIR|g" "$REPO_DIR/services/localllm-llm.service")"
if [[ -f "$UNIT" ]] && [[ "$RENDERED" == "$(cat "$UNIT")" ]]; then
    echo "localllm-llm.service is already installed."
else
    printf '%s\n' "$RENDERED" | sudo tee "$UNIT" >/dev/null
    sudo systemctl daemon-reload
    echo "Installed $UNIT"
    if systemctl is-active --quiet localllm-llm; then
        echo "localllm-llm is running with the old unit: run stop/llama.sh && start/llama.sh to apply it."
    fi
fi
