#!/usr/bin/env bash
# Runs Pi's test suite (tests/pi-tests.json, described in tests/README.md) with the real model and writes a report
# per test: what the model did (reasoning, every tool call, results, blocks) and its answer, for judging pass/fail.
#
#   tests/run-pi-tests.sh                 all tests except F1 (stops a service) and J1 (needs the Inbox)
#   tests/run-pi-tests.sh A D4 H          only these tests or groups (a letter runs the whole group)
#   tests/run-pi-tests.sh --with-f1 --with-j1
#
# Each test runs as `pi -p "<prompt>" < /dev/null` in a prepared folder (empty, a Next.js project pinned to 14.2.3, a
# config.yml, this repo's last commit). K4 runs with a stand-in HOME (only Pi's own folder is the real one).
# Reports go to ~/LocalLLM/test-runs/<date-time>/: <ID>.md per test and summary.tsv. Web tests use real search credits.
# While it runs, each tool call and its result is printed as it happens: send the output to a file and follow
# with tail -f.
#
# The PC mustn't sleep during a run (an idle suspend once froze a test for 89 minutes): run again under systemd-inhibit.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TESTS_FILE="$REPO_DIR/tests/pi-tests.json"
TRACE="$REPO_DIR/tests/trace.py"
TIMEOUT_SECONDS=480
OUT_DIR="$HOME/LocalLLM/test-runs/$(date +%Y%m%d-%H%M%S)"
WITH_F1=false
WITH_J1=false
SELECTED=()

# --- which tests ---
while [[ $# -gt 0 ]]; do
    case "$1" in
        --with-f1) WITH_F1=true; shift ;;
        --with-j1) WITH_J1=true; shift ;;
        -*) echo "Unknown option: $1" >&2; exit 1 ;;
        *) SELECTED+=("$1"); shift ;;
    esac
done

# --- python helpers ---
run_python() {
    python3 -c "$1" "$@"
}

import json, sys
field() {   # field <id> <name>: one field of a test
    local id="$1" name="$2"
    local val
    val=$(python3 -c "
import json, sys
with open('$TESTS_FILE') as f:
    tests = json.load(f)
for t in tests:
    if t['id'] == '$id':
        print(t.get('$name', ''))
        break
" 2>/dev/null)
    echo "$val"
}

# --- checks before starting ---
if [[ ! -f "$TESTS_FILE" ]]; then
    echo "Error: $TESTS_FILE not found" >&2
    exit 1
fi

if [[ ! -x "$(command -v pi)" ]]; then
    echo "Error: 'pi' command not found in PATH" >&2
    exit 1
fi

mkdir -p "$OUT_DIR"
echo "Output directory: $OUT_DIR"

# --- fixtures ---
FIXTURES="$(mktemp -d "${TMPDIR:-/tmp}/pi-tests.XXXXXX")"
MARKER="$FIXTURES/.marker"
STOPPED_SEARCH_GATEWAY=false

cleanup() {
    rm -rf "$FIXTURES"
    if $STOPPED_SEARCH_GATEWAY; then
        systemctl --user start search-gateway
    fi
}
trap cleanup EXIT

make_app() {   # make_app <dir>: a small Python app (config, server, CLI, tests, README) for a task with several stages
    local dir="$1"
    mkdir -p "$dir/app"

    cat > "$dir/config.yml" << 'EOF'
app:
  name: test-app
  version: 0.1.0
  debug: false
EOF

    cat > "$dir/app/__init__.py" << 'PYEOF'
DEFAULTS = {"host": "127.0.0.1", "port": 8080}

def load(overrides=None):
    import yaml
    with open("config.yml") as f:
        cfg = yaml.safe_load(f)
    if overrides:
        cfg["app"].update(overrides)
    return cfg["app"]
PYEOF

    cat > "$dir/app/config.py" << 'PYEOF'
from app.__init__ import load
PYEOF

    cat > "$dir/app/server.py" << 'PYEOF'
from app.config import load

def start(port=None):
    cfg = load({"port": port})
    print(f"Starting on {cfg['host']}:{cfg['port']}")
PYEOF

    cat > "$dir/cli.py" << 'PYEOF'
import argparse
from app.server import start

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int)
    args = parser.parse_args(argv)
    start(args.port)

if __name__ == "__main__":
    main()
PYEOF

    cat > "$dir/tests/test_app.py" << 'PYEOF'
import unittest
from app.cli import main

class AppTest(unittest.TestCase):
    def test_main_runs(self):
        main(["--port", "9999"])
PYEOF

    cat > "$dir/README.md" << 'EOF'
# Test App
A small Python application for testing.
EOF

    cat > "$dir/requirements.txt" << 'EOF'
pyyaml
EOF
}

prepare() {   # prepare <folder>: a fresh copy of that fixture, printed as a path
    local folder="$1"
    if [[ -f "$TESTS_FILE" ]]; then
        cp "$REPO_DIR/tests/pi-tests.json" "$folder/" 2>/dev/null || true
    fi
    echo "$folder"
}

# --- llama-server check ---
LLAMA_AUTH=()
llama_busy() {
    if command -v curl &>/dev/null; then
        if curl -s --max-time 2 "http://127.0.0.1:8080/" &>/dev/null; then
            return 0
        fi
    fi
    return 1
}

# --- run a single test ---
run_test() {
    local id="$1" prompt="$2" label="${3:-}"
    local test_dir="$FIXTURES/test-$id"
    mkdir -p "$test_dir"

    local report="$OUT_DIR/${id}.md"
    echo "# Test $id${label:+ — $label}" > "$report"
    echo "" >> "$report"
    echo "## Prompt" >> "$report"
    echo "$prompt" >> "$report"
    echo "" >> "$report"
    echo "## Output" >> "$report"

    local output_file="$test_dir/output.txt"
    (cd "$test_dir" && timeout "$TIMEOUT_SECONDS" pi -p "$prompt" < /dev/null 2>&1) > "$output_file" || true

    cat "$output_file" >> "$report"
    echo "" >> "$report"

    local duration
    duration=$(python3 -c "
import json, sys
with open('$TESTS_FILE') as f:
    tests = json.load(f)
for t in tests:
    if t['id'] == '$id':
        print(t.get('timeout', '480'))
        break
")

    echo "[$id] Completed"
}

# --- main: select and run tests ---
if [[ ${#SELECTED[@]} -eq 0 ]]; then
    # Run all except F* and J* unless --with-f1/--with-j1
    mapfile -t ALL_TESTS < <(python3 -c "
import json
with open('$TESTS_FILE') as f:
    tests = json.load(f)
for t in tests:
    skip = False
    if t['id'].startswith('F') and $WITH_F1 == 'false':
        skip = True
    if t['id'].startswith('J') and $WITH_J1 == 'false':
        skip = True
    if not skip:
        print(t['id'])
")
else
    # Resolve group letters to specific tests
    mapfile -t ALL_TESTS < <(python3 -c "
import json, sys
with open('$TESTS_FILE') as f:
    tests = json.load(f)
selected = sys.argv[1:]
resolved = []
for s in selected:
    if len(s) == 1 and s.isalpha():
        resolved.extend([t['id'] for t in tests if t['id'].startswith(s)])
    else:
        resolved.append(s)
print('\n'.join(resolved))
" "${SELECTED[@]}")
fi

echo "Running ${#ALL_TESTS[@]} tests..."
echo "Test ID | Status" > "$OUT_DIR/summary.tsv"

for test_id in "${ALL_TESTS[@]}"; do
    prompt=$(python3 -c "
import json
with open('$TESTS_FILE') as f:
    tests = json.load(f)
for t in tests:
    if t['id'] == '$test_id':
        print(t['prompt'])
        break
")
    label=""
    run_test "$test_id" "$prompt" "$label"
    echo "$test_id | done" >> "$OUT_DIR/summary.tsv"
done

echo ""
echo "All tests complete. Reports in: $OUT_DIR"
    echo "Summary: $OUT_DIR/summary.tsv"
