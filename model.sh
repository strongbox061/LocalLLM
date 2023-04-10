#!/usr/bin/env bash
# The active model profile (models/<name>.env). Source it, don't run it; it doesn't need lib/common.sh, so the
# shellcheck disable is only on this file.
#   load_model_profile          sets the profile's values (MODEL_*, CTX_PER_SLOT...), local.env's overrides applied
#   llama_server_args           command-line flags for llama.cpp's main binary
#   model_json / write_model_json   the profile as JSON for Pi's extensions (~/.config/localllm/model.json)
# The variables here are used by the scripts that source this file.
# shellcheck disable=SC2034

MODEL_REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

MODEL_ALIAS="local"

MODELS_HOME="$HOME/LocalLLM/models"
MODEL_JSON_FILE="${LOCALLLM_MODEL_JSON:-$HOME/.config/localllm/model.json}"
# This machine's own settings: MODEL_PROFILE and the LLAMA_* overrides.
# shellcheck source=/dev/null

load_model_profile() {
  local profile_name="${1:-}"

  if [[ -n "$profile_name" ]]; then
    # Accept a name or a path to a profile .env file
    if [[ -f "$profile_name" ]]; then
      PROFILE_PATH="$profile_name"
    elif [[ -f "$MODELS_HOME/$profile_name.env" ]]; then
      PROFILE_PATH="$MODELS_HOME/$profile_name.env"
    else
      echo "load_model_profile: profile '$profile_name' not found" >&2
      return 1
    fi
  else
    # Read from local.env in the repo root, fall back to models/default
    if [[ -f "$MODEL_REPO_DIR/local.env" ]]; then
      PROFILE_PATH="$MODELS_HOME/$(. "$MODEL_REPO_DIR/local.env" && echo "${MODEL_PROFILE:-default}").env"
    else
      PROFILE_PATH="$MODELS_HOME/default.env"
    fi
  fi

  if [[ ! -f "$PROFILE_PATH" ]]; then
    echo "load_model_profile: cannot find profile at '$PROFILE_PATH'" >&2
    return 1
  fi

  # Source the profile to get MODEL_REPO, MODEL_FILE, MODEL_NAME, etc.
  # shellcheck source=/dev/null
  . "$PROFILE_PATH"

  # Apply any LLAMA_* overrides from local.env (if it exists and we haven't sourced the profile through it)
  if [[ -f "$MODEL_REPO_DIR/local.env" && -z "${1:-}" ]]; then
    . "$MODEL_REPO_DIR/local.env"
  fi

  # Defaults for fields a profile might omit
  MODEL_NAME="${MODEL_NAME:-$(basename "${MODEL_FILE%.bin}")}"
  CTX_PER_SLOT="${CTX_PER_SLOT:-2048}"
  SLOTS="${SLOTS:-1}"
  AT_ONCE="${AT_ONCE:-1}"
  SUB_MAX="${SUB_MAX:-5}"
  MAX_TOKENS="${MAX_TOKENS:-512}"
  REASONING="${REASONING:-off}"
  REASONING_OFF_EFFORT="${REASONING_OFF_EFFORT:-fast}"
  VISION="${VISION:-off}"

  MODEL_PROFILE="$(basename "${PROFILE_PATH%.env}")"
}

llama_server_args() {
  local args=()
  args+=("--model" "$MODEL_FILE")
  args+=("--ctx-size" "$CTX_PER_SLOT")
  args+=("--threads" "$(nproc)")
  args+=("--threads-batch" "$(nproc)")
  [[ "$SLOTS" -gt 1 ]] && args+=("--parallel" "$SLOTS")
  [[ "$MAX_TOKENS" -gt 0 ]] && args+=("--max-tokens" "$MAX_TOKENS")
  echo "${args[@]}"
}

model_json() {
  local q='"'
  local vision="false"
  [[ "$VISION" == "on" ]] && vision="true"
  local old=""
  [[ -n "${OLD_ALIASES:-}" ]] && old=$(echo "$OLD_ALIASES" | tr ',' '\n' | while read -r a; do
    echo "\"${a//$q/}\","
  done | tr '\n' ' ')

  cat <<EOF
{
  "profile": "${MODEL_PROFILE//$q/}",
  "name": "${MODEL_NAME//$q/}",
  "alias": "$MODEL_ALIAS",
  "reasoning": "$REASONING",
  "reasoningOffEffort": "${REASONING_OFF_EFFORT//$q/}",
  "contextPerSlot": $CTX_PER_SLOT,
  "slots": $SLOTS,
  "subagentsAtOnce": $AT_ONCE,
  "subagentsMax": $SUB_MAX,
  "maxTokens": $MAX_TOKENS,
  "vision": $vision,
  "oldAliases": [$old]
}
EOF
}

write_model_json() {
  local new_json
  new_json="$(model_json)"
  local old_json=""
  if [[ -f "$MODEL_JSON_FILE" ]]; then
    old_json="$(cat "$MODEL_JSON_FILE")"
  fi

  if [[ "$new_json" != "$old_json" ]]; then
    mkdir -p "$(dirname "$MODEL_JSON_FILE")"
    echo "$new_json" > "$MODEL_JSON_FILE"
    return 0
  fi
  return 1
}
