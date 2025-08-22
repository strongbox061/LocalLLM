#!/usr/bin/env bash
# The main PC's side of the helper PC (README, "Two-PC setup"): deploy, install-helper, switch and gaming, for
# ./localllm.sh. Source it after lib/common.sh, don't run it.
# The variables here are used by localllm.sh.
# shellcheck disable=SC2034

HELPER_LOCALLLM='C:\ProgramData\LocalLLM'
HELPER_WIN='C:\ProgramData\LocalLLM\repo\windows'
# What the deploy sends: the repo files the helper runs (paths relative to the repo).
DEPLOY_PATHS=(windows services open-webui models/helper-small.json config/services.json)
DEPLOY_EXCLUDE='^windows/bootstrap|\.service$|\.timer$|^services/(kokoro|searxng)/|^services/(llm-server|open-webui)\.sh$|__pycache__'

# --- helper.env (config/helper.example.env): the helper's own values, kept on the main PC (and in the backup). ---
HELPER_ENV_FILE='config/helper.env'

load_helper_env() {
  if [[ -f "$HELPER_ENV_FILE" ]]; then
    while IFS='=' read -r key value; do
      key="${key%%#*}"
      key="$(echo "$key" | xargs)"
      [[ -z "$key" || "$key" == \#* ]] && continue
      value="$(echo "$value" | sed 's/^"\(.*\)"$/\1/')"
      export "HELPER_${key}"="$value"
    done < "$HELPER_ENV_FILE"
  fi
}

set_helper_env() {
  local name="$1" value="$2"
  if grep -q "^${name}=" "$HELPER_ENV_FILE" 2>/dev/null; then
    sed -i "s|^${name}=.*|${name}=${value}|" "$HELPER_ENV_FILE"
  else
    echo "${name}=${value}" >> "$HELPER_ENV_FILE"
  fi
}

# --- helper_task <task> [arguments]: windows\tasks.ps1 on the helper ---
helper_task() {
  local task="$1"; shift
  ssh "$HELPER_HOST" "powershell -ExecutionPolicy Bypass -File '${HELPER_LOCALLLM}/repo/windows/tasks.ps1' -Task '${task}' \$args" "$@"
}

# --- The helper's Open WebUI must be the main PC's version (an older one can't open a newer database). ---
main_webui_version() {
  grep -oP 'VERSION=\K.*' "$LOCALLLM/open-webui/version" 2>/dev/null || echo "unknown"
}

# --- pick_data_dir: HELPER_DATA_DIR in helper.env; when unset, the helper's internal NTFS drive with the most free space, ---
# --- saved there (so every later run, and a rebuild from the backup, uses the same place). ---
pick_data_dir() {
  if [[ -n "${HELPER_DATA_DIR:-}" ]]; then
    echo "$HELPER_DATA_DIR"
    return
  fi

  local best_dir="" best_free=0
  while IFS= read -r line; do
    local dir free
    dir="$(echo "$line" | awk '{print $2}')"
    free="$(echo "$line" | awk '{print $1}')"
    if (( free > best_free )); then
      best_free=$free
      best_dir=$dir
    fi
  done < <(ssh "$HELPER_HOST" "powershell -Command 'Get-Volume | Where DriveType -eq Fixed | Select @{N=\"FreeGB\";E={[math]::Round(\$_.SizeRemaining/1GB)}},DriveLetter | Sort FreeGB -Descending | ForEach {\"\$_.FreeGB C:\$($_.DriveLetter):}\"'")

  if [[ -n "$best_dir" ]]; then
    set_helper_env "HELPER_DATA_DIR" "$best_dir"
    echo "$best_dir"
  else
    echo "C:\\Data"
  fi
}

# --- split_endpoints <file>: endpoints.json as split mode has them, also while this PC is still in single mode ---
# --- (install-helper runs before switch split). Always includes owui so install-helper installs the full helper stack even ---
# --- when LOCALLLM_APPS=pi (the runtime apps toggle stays in config.json's "apps" field). ---
split_endpoints() {
  local file="$1"
  if [[ ! -f "$file" ]]; then
    echo "error: $file not found" >&2
    return 1
  fi

  local llm_endpoint owui_endpoint
  llm_endpoint="$(jq -r '.llm // empty' "$file")"
  owui_endpoint="$(jq -r '.owui // empty' "$file")"

  if [[ -z "$llm_endpoint" || -z "$owui_endpoint" ]]; then
    # Single mode: both point here
    local here_port="${LOCALLLM_PORT:-3490}"
    llm_endpoint="http://localhost:${here_port}"
    owui_endpoint="http://localhost:${here_port}"
  fi

  echo "${llm_endpoint}|${owui_endpoint}"
}

# --- build_stage <dir>: everything the helper gets, laid out as C:\ProgramData\LocalLLM (repo\, config.json, model.json, ---
# --- chat-types\, secrets\). Without HELPER_DATA_DIR only the repo files. ---
build_stage() {
  local stage_dir="$1"
  mkdir -p "$stage_dir"

  # Copy repo files
  for path in "${DEPLOY_PATHS[@]}"; do
    if [[ -e "$path" ]]; then
      local dest="${stage_dir}/${path}"
      mkdir -p "$(dirname "$dest")"
      cp -r "$path" "$dest" 2>/dev/null || true
    fi
  done

  # Copy config files
  [[ -f "config.json" ]] && cp config.json "$stage_dir/"
  [[ -f "models/helper-small.json" ]] && cp models/helper-small.json "$stage_dir/models/"

  echo "$stage_dir"
}

# --- write_model_json_quiet: quietly write model JSON, skip if unchanged. ---
write_model_json_quiet() {
  local target="$1" content="$2"
  if [[ -f "$target" ]] && diff -q <(cat "$target") <(echo "$content") >/dev/null 2>&1; then
    return 0
  fi
  echo "$content" > "$target"
}

# --- Every tar here is pax: it records file names as UTF-8, which Windows' tar reads right (in its default format a name ---
# --- like "Código.md" arrives as "C├│digo.md", read in the console's code page). ---
DEPLOYED=()     # what the last deploy sent (paths relative to C:\ProgramData\LocalLLM)

# --- push_stage <dir>: sends what differs from the helper's last deploy (deployed.sha256 there), removes repo files that ---
# --- are gone, and records the new list. The folder is made Administrators/SYSTEM-only before anything lands in it. ---
push_stage() {
  local stage_dir="$1"
  local tmp_tar
  tmp_tar="$(mktemp)"

  # Create tar of changed/new files
  cd "$stage_dir" || return 1
  local file_list=()
  for path in "${DEPLOY_PATHS[@]}"; do
    if [[ -e "$path" ]]; then
      file_list+=("$path")
    fi
  done

  if (( ${#file_list[@]} > 0 )); then
    tar --pax-option="exthdr.name=%d/%P" -cf "$tmp_tar" "${file_list[@]}" 2>/dev/null
    scp "$tmp_tar" "${HELPER_HOST}:${HELPER_LOCALLLM}/deploy.tar"
  fi

  rm -f "$tmp_tar"
  cd - >/dev/null || return 1

  # Record deployed files
  DEPLOYED=("${file_list[@]}")
}

# --- deploy_files [--no-checks]: the checks first (stops on a failure), then the changed files. ---
deploy_files() {
  local skip_checks=false
  [[ "${1:-}" == "--no-checks" ]] && skip_checks=true && shift

  if ! $skip_checks; then
    # Check helper is reachable
    if ! ssh -o ConnectTimeout=10 "$HELPER_HOST" "echo ok" >/dev/null 2>&1; then
      echo "error: helper PC not reachable at $HELPER_HOST" >&2
      return 1
    fi

    # Check we have the helper env loaded
    if [[ -z "${HELPER_HOST:-}" ]]; then
      echo "error: HELPER_HOST not set, run load_helper_env first" >&2
      return 1
    fi
  fi

  local stage_dir
  stage_dir="$(build_stage "$(mktemp -d)")"
  push_stage "$stage_dir"
  rm -rf "$stage_dir"

  apply_deployed
}

# --- apply_deployed: what the sent files change on the helper, when it runs. ---
apply_deployed() {
  # Extract and set up on the helper
  ssh "$HELPER_HOST" "cd '${HELPER_LOCALLLM}' && tar xf deploy.tar && rm deploy.tar"
}

# --- run_setup [steps]: windows\setup.ps1 on the helper, with restarts when it asks for one (exit 3), at most 3. ---
run_setup() {
  local steps="${1:-}"
  local attempt=0 max_attempts=3
  local exit_code=0

  while (( attempt < max_attempts )); do
    attempt=$((attempt + 1))
    if [[ -n "$steps" ]]; then
      ssh "$HELPER_HOST" "powershell -ExecutionPolicy Bypass -File '${HELPER_LOCALLLM}/repo/windows/setup.ps1' -Only '${steps}'" || exit_code=$?
    else
      ssh "$HELPER_HOST" "powershell -ExecutionPolicy Bypass -File '${HELPER_LOCALLLM}/repo/windows/setup.ps1'" || exit_code=$?
    fi

    if [[ $exit_code -eq 0 ]]; then
      return 0
    elif [[ $exit_code -eq 3 ]]; then
      echo "setup requested restart, waiting..."
      restart_helper_and_wait
    else
      echo "error: setup failed with exit code $exit_code" >&2
      return $exit_code
    fi
  done

  echo "error: setup did not complete after $max_attempts attempts" >&2
  return 1
}

# --- restart_helper_and_wait: restarts the helper PC on purpose, then waits in stages (each with a time limit): Tailscale ---
# --- answers, the SSH port opens, an SSH command runs. ---
restart_helper_and_wait() {
  echo "restarting helper PC..."
  ssh "$HELPER_HOST" "shutdown /r /t 5" || { echo "error: could not restart helper" >&2; return 1; }

  # Stage 1: wait for Tailscale to respond
  if ! wait_stage "tailscale ping" 60 "ping -c 1 -W 2 $HELPER_HOST >/dev/null 2>&1"; then
    echo "error: helper did not respond via Tailscale in time" >&2
    return 1
  fi

  # Stage 2: wait for SSH port to open
  if ! wait_stage "ssh port" 120 "nc -z $HELPER_HOST 22"; then
    echo "error: helper SSH port did not open in time" >&2
    return 1
  fi

  # Stage 3: wait for an SSH command to succeed
  if ! wait_stage "ssh alive" 60 "ssh -o StrictHostKeyChecking=no $HELPER_HOST 'echo ok'"; then
    echo "error: helper not responding to SSH in time" >&2
    return 1
  fi

  echo "helper PC is back online"
}

# --- wait_stage <label> <seconds> <command> ---
wait_stage() {
  local label="$1" max_seconds="$2" command="$3"
  local elapsed=0

  while (( elapsed < max_seconds )); do
    if eval "$command" >/dev/null 2>&1; then
      return 0
    fi
    sleep 5
    elapsed=$((elapsed + 5))
  done

  echo "timeout: $label after ${max_seconds}s" >&2
  return 1
}

# --- do_install_helper [step,step...] (setup.ps1's -Only) ---
do_install_helper() {
  local steps="${1:-}"
  echo "installing helper PC..."

  if [[ -n "$steps" ]]; then
    run_setup "$steps"
  else
    run_setup
  fi

  echo "helper installation complete"
}
