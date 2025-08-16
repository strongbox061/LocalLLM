#!/usr/bin/env bash
# Installs Pi and the llama.cpp provider in models.json (sized from the model profile), and puts ~/.local/bin on PATH
# for the desktop session from the next login (~/.config/environment.d/60-localllm-path.conf).
#   install/pi.sh            only if Pi is missing
#   install/pi.sh --update   install the latest Pi
#   A fresh install uses the version recorded by the last backup (~/.config/localllm/versions.env), if any.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

ENV_FILE="$HOME/.config/environment.d/60-localllm-path.conf"
# Written literally: systemd expands ${HOME} and ${PATH} at login.
# shellcheck disable=SC2016
ENV_LINE='PATH=${HOME}/.local/bin:${PATH}'

DDGS_PTH="$(python3 -m site --user-site)/pi-ddgs.pth"
# Its command link can stay behind, pointing nowhere.

# --- Helpers ---

ensure_env_file() {
  mkdir -p "$(dirname "$ENV_FILE")"
  if [[ ! -f "$ENV_FILE" ]]; then
    printf '%s\n' "$ENV_LINE" > "$ENV_FILE"
    log_info "Created $ENV_FILE"
  fi
}

write_models_entry() {
  local model_file="$1"
  local model_name="$2"
  local model_size="$3"

  local models_json="$HOME/.config/pi/models.json"
  mkdir -p "$(dirname "$models_json")"

  if [[ ! -f "$models_json" ]]; then
    printf '{}\n' > "$models_json"
  fi

  local size_bytes
  size_bytes=$(numfmt --to=iec "$model_size" 2>/dev/null || echo "$model_size")

  cat > "$models_json" <<EOF
{
  "models": [
    {
      "name": "${model_name}",
      "provider": "llama.cpp",
      "path": "${model_file}",
      "parameters": {
        "size": "${size_bytes}"
      }
    }
  ]
}
EOF
  log_info "Wrote llama.cpp provider entry for ${model_name} in $models_json"
}

install_pi() {
  local target_dir="$HOME/.local/lib/node_modules/@earendil-works/pi-coding-agent"

  if [[ -d "$target_dir/package.json" ]] || [[ -f "$target_dir/package.json" ]]; then
    log_info "Pi already installed at $target_dir"
    return 0
  fi

  local install_version=""
  if [[ "${1:-}" == "--update" ]]; then
    log_info "Installing latest Pi"
  else
    install_version="$(get_saved_version pi 2>/dev/null || true)"
    if [[ -n "$install_version" ]]; then
      log_info "Using saved Pi version: $install_version"
    fi
  fi

  log_info "Installing Pi to $target_dir"
  npm install --prefix "$HOME/.local/lib" "@earendil-works/pi-coding-agent${install_version:+@$install_version}"
  log_info "Pi installed successfully"
}

cleanup_ddgs() {
  if [[ -L "$DDGS_PTH" ]]; then
    rm -f "$DDGS_PTH"
    log_info "Removed stale DDGS path file at $DDGS_PTH"
  fi
}

# --- Main ---

main() {
  ensure_env_file
  install_pi "${1:-}"
  cleanup_ddgs

  local model_file="${LOCALLLM_MODEL_FILE:-}"
  local model_name="${LOCALLLM_MODEL_NAME:-Qwen3-30B-A3B-Instruct-2507}"
  local model_size="${LOCALLLM_MODEL_SIZE:-}"

  if [[ -n "$model_file" && -n "$model_size" ]]; then
    write_models_entry "$model_file" "$model_name" "$model_size"
  else
    log_warn "Model file or size not set — skipping models.json entry (run with model config to add it)"
  fi

  log_info "Done. PATH updated for next login via $ENV_FILE"
}

main "$@"
