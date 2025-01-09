#!/usr/bin/env bash
# The active model profile (models/<name>.env). Source it, don't run it; it doesn't need lib/common.sh, so the
# llama-server service script can use it too.
# shellcheck disable=SC2034
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
[[ -f "$HOME/.config/localllm/local.env" ]] && source "$HOME/.config/localllm/local.env"

# load_model_profile [name|path]: the given profile, else MODEL_PROFILE from local.env, else models/default.
# Prints what is wrong and returns 1 when the profile is missing or incomplete.
load_model_profile() {
    local name="${1:-${MODEL_PROFILE:-}}"
    [[ -n "$name" ]] || name="$(tr -d '[:space:]' < "$MODEL_REPO_DIR/models/default")"
    local file="$MODEL_REPO_DIR/models/$name.env"
    # A path (with a /) is a profile file elsewhere, e.g. one the tests make.
    [[ "$name" == */* ]] && file="$name" && name="$(basename -- "$name" .env)"
    if [[ ! -f "$file" ]]; then
        local known=() f
        for f in "$MODEL_REPO_DIR"/models/*.env; do known+=("$(basename -- "$f" .env)"); done
        echo "ERROR: no model profile $file (MODEL_PROFILE in ~/.config/localllm/local.env; profiles: ${known[*]})" >&2
        return 1
    fi
    # Nothing left over from another profile.
    MODEL_NAME="" MODEL_REPO="" MODEL_FILE="" MMPROJ_FILE="" MMPROJ_LOCAL="" CTX_PER_SLOT="" SLOTS="" MAX_TOKENS=""
    CACHE_RAM_MIB="" MOE_LAYERS="" N_CPU_MOE="" REASONING="" REASONING_OFF_EFFORT="" REASONING_BUDGET=""
    PRESERVE_THINKING="" SAMPLING=() OLD_ALIASES=() AT_ONCE=1 SUB_MAX=8
    # shellcheck source=/dev/null
    source "$file"
    MODEL_PROFILE="$name"
    MODEL_PROFILE_FILE="$file"

    local problems=() v
    for v in MODEL_NAME MODEL_REPO MODEL_FILE; do
        [[ -n "${!v}" ]] || problems+=("$v is empty")
    done
    for v in CTX_PER_SLOT SLOTS MAX_TOKENS CACHE_RAM_MIB; do
        [[ "${!v}" =~ ^[1-9][0-9]*$ ]] || problems+=("$v must be a number")
    done
    for v in MOE_LAYERS N_CPU_MOE REASONING_BUDGET; do
        [[ -z "${!v}" || "${!v}" =~ ^[0-9]+$ ]] || problems+=("$v must be a number or empty")
    done
    [[ "$MAX_TOKENS" =~ ^[0-9]+$ && "$CTX_PER_SLOT" =~ ^[0-9]+$ && $MAX_TOKENS -lt $CTX_PER_SLOT ]] \
        || problems+=("MAX_TOKENS must be less than CTX_PER_SLOT")
    if [[ -n "$MMPROJ_FILE" && -z "$MMPROJ_LOCAL" ]] || [[ -z "$MMPROJ_FILE" && -n "$MMPROJ_LOCAL" ]]; then
        problems+=("MMPROJ_FILE and MMPROJ_LOCAL go together")
    fi
    case "$REASONING" in
        qwen-template | none) ;;
        reasoning-effort) REASONING_OFF_EFFORT="${REASONING_OFF_EFFORT:-low}" ;;
        *) problems+=("REASONING must be qwen-template, reasoning-effort or none") ;;
    esac
    [[ -z "$PRESERVE_THINKING" || "$PRESERVE_THINKING" == true || "$PRESERVE_THINKING" == false ]] \
        || problems+=("PRESERVE_THINKING must be true, false or empty")
    [[ -z "${SUBAGENTS_AT_ONCE:-}" || "$SUBAGENTS_AT_ONCE" =~ ^[1-9][0-9]*$ ]] \
        || problems+=("SUBAGENTS_AT_ONCE (local.env) must be a number from 1")
    [[ -z "${SUBAGENTS_MAX:-}" || "$SUBAGENTS_MAX" =~ ^[1-9][0-9]*$ ]] \
        || problems+=("SUBAGENTS_MAX (local.env) must be a number from 1")
    if [[ ${#problems[@]} -gt 0 ]]; then
        printf 'ERROR: model profile %s: %s\n' "$file" "${problems[@]}" >&2
        return 1
    fi

    # This PC's values win over the profile's defaults.
    [[ -n "$N_CPU_MOE" ]] && N_CPU_MOE="${LLAMA_N_CPU_MOE:-$N_CPU_MOE}"
    CACHE_RAM_MIB="${LLAMA_CACHE_RAM_MIB:-$CACHE_RAM_MIB}"
    # Pi-only mode (LOCALLLM_APPS=pi): Open WebUI is off, so give all of llama-server's context to Pi's single slot.
    if [[ "${LOCALLLM_APPS:-pi,owui}" == pi && "$SLOTS" -gt 1 ]]; then
        CTX_PER_SLOT=$((CTX_PER_SLOT * SLOTS))
        SLOTS=1
    fi
    # Pi's subagents at once (local.env): 1 = one after another; never more than llama-server's slots.
    AT_ONCE="${SUBAGENTS_AT_ONCE:-1}"
    if (( AT_ONCE > SLOTS )); then
        echo "WARNING: SUBAGENTS_AT_ONCE=$AT_ONCE, but the profile has $SLOTS slots: $SLOTS at once." >&2
        AT_ONCE=$SLOTS
    fi
    # The most subagents one message may start (one after another, so not capped at the slots).
    SUB_MAX="${SUBAGENTS_MAX:-8}"
    MODEL_PATH="$MODELS_HOME/$MODEL_FILE"
    MMPROJ_PATH="${MMPROJ_LOCAL:+$MODELS_HOME/$MMPROJ_LOCAL}"
    return 0
}

llm_server_args() {
  LLM_ARGS=(-m "$MODEL_FILE")
  LLM_ARGS+=(-c "$CTX_PER_SLOT" --threads "$(nproc)" --threads-batch "$(nproc)")
  [[ "$SLOTS" -gt 1 ]] && LLM_ARGS+=(--parallel "$SLOTS")
  [[ "$MAX_TOKENS" -gt 0 ]] && LLM_ARGS+=(--max-tokens "$MAX_TOKENS")
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
