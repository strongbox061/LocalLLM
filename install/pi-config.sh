#!/usr/bin/env bash
# Copies this repo's Pi files (pi/) into ~/.pi/agent: extensions, AGENTS.md, plannotator.json, agents, prompts and
# skills (only what differs).
# Run it again after editing anything in pi/.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

PI_SOURCE_DIR="$REPO_DIR/pi"

# The extensions' word rules (extensions/lang-words.ts, imported by the others) are generated from
# lang/phrases.json: never install a stale copy.
python3 "$REPO_DIR/lang/build.py" --check >/dev/null || die "lang/phrases.json changed: run python3 lang/build.py first."

# --- extensions: single-file extensions (.ts/.js) and extension directories ---
mkdir -p "$PI_AGENT_DIR/extensions"
for item in "$PI_SOURCE_DIR/extensions"/*; do
    [[ -e "$item" ]] || continue
    if [[ -d "$item" ]] || [[ "$item" == *.ts ]] || [[ "$item" == *.js ]]; then
        target="$PI_AGENT_DIR/extensions/$(basename -- "$item")"
        if [[ -e "$target" ]] && diff -rq "$item" "$target" >/dev/null 2>&1; then
            echo "Same: extensions/$(basename -- "$item")"
        else
            cp -a "$item" "$PI_AGENT_DIR/extensions/"
            echo "Copied: extensions/$(basename -- "$item")"
        fi
    fi
done

# --- single files: keep the existing one as a backup if it differs ---
copy_file() {    # copy_file <name in pi/ and in ~/.pi/agent>
    local source_file="$PI_SOURCE_DIR/$1" target_file="$PI_AGENT_DIR/$1" backup
    [[ -f "$source_file" ]] || die "Not found: $source_file"
    if [[ -f "$target_file" ]] && cmp -s "$source_file" "$target_file"; then
        echo "Same: $1"
        return 0
    fi
    if [[ -f "$target_file" ]]; then
        backup="$target_file.bak.$(date +%Y%m%d-%H%M%S)"
        cp -a "$target_file" "$backup"
        echo "Backed up existing file to: $backup"
    fi
    cp -a "$source_file" "$target_file"
    echo "Copied: $1"
}

copy_file AGENTS.md
# Plannotator's plan mode: its planning instructions send plans to the Inbox instead of the review tab.
copy_file plannotator.json

# --- subagents (extensions/subagent) and the workflows that use them (/implement...): one .md file each ---
for dir in agents prompts; do
    mkdir -p "$PI_AGENT_DIR/$dir"
    for item in "$PI_SOURCE_DIR/$dir"/*.md; do
        [[ -f "$item" ]] && copy_file "$dir/$(basename -- "$item")"
    done
done
# Renamed since: the old copies would still be loaded (two web_search tools) or offered to the model.
for old in agents/scout.md prompts/scout-and-plan.md extensions/local-web-research.ts; do
    if [[ -f "$PI_AGENT_DIR/$old" ]]; then
        rm -- "$PI_AGENT_DIR/$old"
        echo "Removed (renamed): $old"
    fi
done

# --- compaction in settings.json: only these keys are set, the rest of the file is kept ---
# When a session's context gets full, Pi summarizes the older part instead of stopping. Sized from the model profile:
# it starts when less than one full answer plus room for a tool result (MAX_TOKENS + 4096) is free, and keeps the
# last fifth of the context as it is. 12288 and 11468 with the default profile's 57,344-token context.
# The model profile and this PC's settings, also written as JSON for the extensions (SUBAGENTS_AT_ONCE...).
source "$REPO_DIR/lib/model.sh"
load_model_profile || exit 1
write_model_json
RESERVE=$((MAX_TOKENS + 4096)) KEEP=$(((CTX_PER_SLOT - MAX_TOKENS) / 5)) python3 - "$PI_AGENT_DIR/settings.json" <<'EOF_PY'
import json, os, sys
path = sys.argv[1]
try:
    with open(path) as f:
        settings = json.load(f)
except FileNotFoundError:
    settings = {}
except ValueError as e:
    sys.exit(f"ERROR: {path} is not valid JSON ({e}); fix it, then run this again.")
want = {"enabled": True, "reserveTokens": int(os.environ["RESERVE"]), "keepRecentTokens": int(os.environ["KEEP"])}
compaction = settings.get("compaction") if isinstance(settings.get("compaction"), dict) else {}
if all(compaction.get(k) == v for k, v in want.items()):
    print("Same: settings.json compaction")
else:
    settings["compaction"] = {**compaction, **want}
    with open(path, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    print(f"Set: settings.json compaction (on, reserve {want['reserveTokens']}, keep {want['keepRecentTokens']})")
EOF_PY

# --- pi-sandbox rules (sandbox.json): the repo's paths are added to what is there, nothing is removed ---
# Writes go only to the project folder and /tmp (pi-sandbox's default; elsewhere the write/edit tools ask, bash is
# blocked). denyWrite blocks, without asking, secrets and ~/.ssh, ~/.gnupg, ~/.pi, ~/.config and the shell's startup
# files even when Pi runs in a folder that contains them (e.g. ~). A denyWrite list replaces pi-sandbox's defaults, so
# it repeats them (.env, *.pem...). Without a UI (pi -p, subagents) whatever would ask is blocked.
python3 - "$PI_SOURCE_DIR/sandbox.json" "$PI_AGENT_DIR/sandbox.json" <<'EOF_PY'
import json, os, sys
source, path = sys.argv[1], sys.argv[2]
with open(source) as f:
    repo = json.load(f)
try:
    with open(path) as f:
        live = json.load(f)
except FileNotFoundError:
    live = {}
except ValueError as e:
    sys.exit(f"ERROR: {path} is not valid JSON ({e}); fix it, then run this again.")
home = os.path.expanduser("~")
expand = lambda p: home + p[1:] if p == "~" or p.startswith("~/") else p
added = []
for section, values in repo.items():
    if not isinstance(values, dict):
        if section not in live:
            live[section] = values
            added.append(section)
        continue
    target = live.setdefault(section, {})
    for key, value in values.items():
        if isinstance(value, list):
            have = target.setdefault(key, [])
            known = {expand(p) for p in have}
            for p in value:
                if expand(p) not in known:
                    have.append(p)
                    known.add(expand(p))
                    added.append(f"{key} {p}")
        elif key not in target:
            target[key] = value
            added.append(f"{section}.{key}")
if added:
    with open(path, "w") as f:
        json.dump(live, f, indent=2)
        f.write("\n")
    print("Added to sandbox.json: " + ", ".join(added))
else:
    print("Same: sandbox.json")
EOF_PY

# --- skills: one directory per skill ---
mkdir -p "$PI_AGENT_DIR/skills"
for item in "$PI_SOURCE_DIR/skills"/*/; do
    [[ -d "$item" ]] || continue
    target="$PI_AGENT_DIR/skills/$(basename -- "$item")"
    if [[ -d "$target" ]] && diff -rq "$item" "$target" >/dev/null 2>&1; then
        echo "Same: skills/$(basename -- "$item")"
    else
        cp -a "$item" "$PI_AGENT_DIR/skills/"
        echo "Copied: skills/$(basename -- "$item")"
    fi
done

echo "Pi files copied to: $PI_AGENT_DIR"
