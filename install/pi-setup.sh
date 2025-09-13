#!/usr/bin/env bash
# install/pi-setup.sh — Bootstrap Pi agent config from the repo.
# Run it again after editing anything in pi/.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

PI_SOURCE_DIR="$REPO_DIR/pi"

# The extensions' word rules (extensions/lang-words.ts, imported by the others) are generated from
# lang/phrases.json: never install a stale copy.

# --- Key declarations / sections / selectors (bodies omitted for v1) ---

# --- extensions: single-file extensions (.ts/.js) and extension directories ---
# --- single files: keep the existing one as a backup if it differs ---
copy_file() {    # copy_file <name in pi/ and in ~/.pi/agent>
  local name="$1"
  local src="$PI_SOURCE_DIR/extensions/$name"
  local dst="$PI_AGENT_DIR/$name"

  if [[ ! -f "$src" ]]; then
    log_warn "Missing extension source: $src"
    return 0
  fi

  if [[ -f "$dst" ]] && cmp -s "$src" "$dst"; then
    return 0
  fi

  if [[ -f "$dst" ]]; then
    cp --backup=t "$dst" "${dst}.bak"
    log_info "Backed up $dst"
  fi

  cp "$src" "$dst"
  log_info "Installed extension: $name"
}

# --- subagents (extensions/subagent) and the workflows that use them (/implement...): one .md file each ---
# Renamed since: the old copies would still be loaded (two web_search tools) or offered to the model.

install_subagent_files() {
  local name="$1"
  local src="$PI_SOURCE_DIR/workflows/$name.md"
  local dst="$PI_AGENT_DIR/prompts/$name.md"

  [[ -f "$src" ]] || return 0
  [[ -f "$dst" ]] && cmp -s "$src" "$dst" && return 0

  cp "$src" "$dst"
  log_info "Installed workflow prompt: $name.md"
}

# --- compaction in settings.json: only these keys are set, the rest of the file is kept ---
# When a session's context gets full, Pi summarizes the older part instead of stopping. Sized from the model profile:
# it starts when less than one full answer plus room for a tool result (MAX_TOKENS + 4096) is free, and keeps the
# last fifth of the context as it is. 12288 and 11468 with the default profile's 57,344-token context.
# The model profile and this PC's settings, also written as JSON for the extensions (SUBAGENTS_AT_ONCE...).
RESERVE=$((MAX_TOKENS + 4096)) KEEP=$(((CTX_PER_SLOT - MAX_TOKENS) / 5)) python3 - "$PI_AGENT_DIR/settings.json" <<'EOF_PY'
import json, os, sys

settings_path = sys.argv[1]
reserve = int(os.environ["RESERVE"])
keep = int(os.environ["KEEP"])

with open(settings_path) as f:
    settings = json.load(f)

# Compaction settings
settings.setdefault("compaction", {})
settings["compaction"]["enabled"] = True
settings["compaction"]["reserve"] = reserve
settings["compaction"]["keep"] = keep

with open(settings_path, "w") as f:
    json.dump(settings, f, indent=2)
    f.write("\n")

print(f"Compaction: reserve={reserve}, keep={keep}")
EOF_PY

# --- pi-sandbox rules (sandbox.json): the repo's paths are added to what is there, nothing is removed ---
# Writes go only to the project folder and /tmp (pi-sandbox's default; elsewhere the write/edit tools ask, bash is
# blocked). denyWrite blocks, without asking, secrets and ~/.ssh, ~/.gnupg, ~/.pi, ~/.config and the shell's startup
# files even when Pi runs in a folder that contains them (e.g. ~). A denyWrite list replaces pi-sandbox's defaults, so
# it repeats them (.env, *.pem...). Without a UI (pi -p, subagents) whatever would ask is blocked.

install_sandbox_rules() {
  local sandbox_cfg="$PI_AGENT_DIR/sandbox.json"

  python3 - "$sandbox_cfg" <<'EOF_PY'
import json, os, sys

config_path = sys.argv[1]
existing = {}
if os.path.isfile(config_path):
    with open(config_path) as f:
        existing = json.load(f)

deny_write = existing.get("denyWrite", [])
# Append known sensitive paths if not already present
extra = [
    ".env",
    "*.pem",
    "*.key",
    str(os.path.expanduser("~/.ssh")),
    str(os.path.expanduser("~/.gnupg")),
    str(os.path.expanduser("~/.pi")),
    str(os.path.expanduser("~/.config")),
]
for path in extra:
    if path not in deny_write:
        deny_write.append(path)

config = existing.copy()
config["denyWrite"] = deny_write

with open(config_path, "w") as f:
    json.dump(config, f, indent=2)
    f.write("\n")
EOF_PY
}

# --- skills: one directory per skill ---
install_skill() {
  local skill_name="$1"
  local src="$PI_SOURCE_DIR/skills/$skill_name"
  local dst="$PI_AGENT_DIR/skills/$skill_name"

  [[ -d "$src" ]] || return 0
  [[ -d "$dst" ]] && return 0

  cp -r "$src" "$dst"
  log_info "Installed skill: $skill_name"
}

# --- Main setup ---
main() {
  log_info "Setting up Pi agent from $PI_SOURCE_DIR"

  # Install single-file extensions
  for ext in "$PI_SOURCE_DIR"/extensions/*.ts "$PI_SOURCE_DIR"/extensions/*.js; do
    [[ -f "$ext" ]] || continue
    copy_file "$(basename "$ext")"
  done

  # Install extension directories
  for dir in "$PI_SOURCE_DIR"/extensions/*/; do
    [[ -d "$dir" ]] || continue
    local dirname
    dirname="$(basename "$dir")"
    log_info "Extension directory: $dirname (skipped for now)"
  done

  # Install workflow prompts
  if [[ -d "$PI_SOURCE_DIR/workflows" ]]; then
    for wf in "$PI_SOURCE_DIR"/workflows/*.md; do
      [[ -f "$wf" ]] || continue
      install_subagent_files "$(basename "$wf" .md)"
    done
  fi

  # Configure compaction
  log_info "Configuring context compaction"

  # Install sandbox rules
  install_sandbox_rules

  # Install skills
  if [[ -d "$PI_SOURCE_DIR/skills" ]]; then
    for skill in "$PI_SOURCE_DIR"/skills/*/; do
      [[ -d "$skill" ]] || continue
      install_skill "$(basename "$skill")"
    done
  fi

  log_info "Pi setup complete"
}

main "$@"
