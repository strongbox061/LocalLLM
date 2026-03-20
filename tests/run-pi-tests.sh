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
# While it runs, each tool call and its result is printed as it happens: send the output to a file and follow it
# with tail -f.
set -uo pipefail

# The PC mustn't sleep during a run (an idle suspend once froze a test for 89 minutes): run again under systemd-inhibit.
if [[ -z "${PI_TESTS_INHIBITED:-}" ]] && command -v systemd-inhibit >/dev/null; then
    PI_TESTS_INHIBITED=1 exec systemd-inhibit --what=sleep:idle --who="LocalLLM tests" \
        --why="Pi model tests are running" "$0" "$@"
fi

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

TESTS_FILE="$REPO_DIR/tests/pi-tests.json"
TRACE="$REPO_DIR/tests/trace.py"
TIMEOUT_SECONDS=480
OUT_DIR="$HOME/LocalLLM/test-runs/$(date +%Y%m%d-%H%M%S)"

WITH_F1=false
WITH_J1=false
SELECTED=()
for arg in "$@"; do
    case "$arg" in
        --with-f1) WITH_F1=true ;;
        --with-j1) WITH_J1=true ;;
        -h | --help) sed -n '2,10p' "$0"; exit 0 ;;
        *) SELECTED+=("$arg") ;;
    esac
done

# --- which tests ---
mapfile -t IDS < <(python3 - "$TESTS_FILE" "$WITH_F1" "$WITH_J1" "${SELECTED[@]}" <<'EOF'
import json, sys
tests = json.load(open(sys.argv[1]))
with_f1, with_j1, wanted = sys.argv[2] == "true", sys.argv[3] == "true", [w.upper() for w in sys.argv[4:]]
for t in tests:
    tid = t["id"]
    if wanted:
        if tid.upper() in wanted or tid[0] in wanted:
            print(tid)
    elif (tid != "F1" or with_f1) and (tid != "J1" or with_j1):
        print(tid)
EOF
)
[[ ${#IDS[@]} -gt 0 ]] || die "No test matches: ${SELECTED[*]}"

field() {   # field <id> <name>: one field of a test
    python3 -c 'import json,sys; t={t["id"]: t for t in json.load(open(sys.argv[1]))}[sys.argv[2]]; print(t.get(sys.argv[3], ""))' \
        "$TESTS_FILE" "$1" "$2"
}

# --- checks before starting ---
command -v pi >/dev/null || die "pi isn't installed."
curl -fsS -m 3 -o /dev/null http://127.0.0.1:8081/health || die "llama-server isn't answering on 127.0.0.1:8081: run ./start.sh."
curl -fsS -m 3 -o /dev/null "$SEARCH_GATEWAY_URL/healthz" || warn "The search gateway isn't running: web tests will fail (./start.sh)."

# --- fixtures ---
FIXTURES="$(mktemp -d "${TMPDIR:-/tmp}/pi-tests.XXXXXX")"
MARKER="$FIXTURES/.marker"
STOPPED_SEARCH_GATEWAY=false
cleanup() {
    if $STOPPED_SEARCH_GATEWAY; then
        echo "Starting the search gateway again..."
        "$REPO_DIR/localllm.sh" start search-gateway >/dev/null || warn "Couldn't restart it: ./localllm.sh start search-gateway."
    fi
    rm -rf "$FIXTURES"
}
trap cleanup EXIT

make_app() {   # make_app <dir>: a small Python app (config, server, CLI, tests, README) for a task with several stages
    mkdir -p "$1/app" "$1/tests"
    : > "$1/app/__init__.py"
    : > "$1/tests/__init__.py"
    cat > "$1/app/config.py" <<'PY'
DEFAULTS = {"host": "127.0.0.1", "port": 8080}


def load(overrides=None):
    settings = dict(DEFAULTS)
    settings.update(overrides or {})
    return settings
PY
    cat > "$1/app/server.py" <<'PY'
from app.config import load


def start(port=None):
    settings = load({"port": port} if port else None)
    return f"serving on {settings['host']}:{settings['port']}"
PY
    cat > "$1/app/cli.py" <<'PY'
import argparse

from app.server import start


def main(argv=None):
    parser = argparse.ArgumentParser(prog="app")
    parser.add_argument("--port", type=int, help="port to listen on (default 8080)")
    args = parser.parse_args(argv)
    return start(port=args.port)


if __name__ == "__main__":
    print(main())
PY
    cat > "$1/tests/test_app.py" <<'PY'
import unittest

from app.cli import main
from app.config import load


class AppTest(unittest.TestCase):
    def test_default(self):
        self.assertEqual(load()["port"], 8080)

    def test_cli_port(self):
        self.assertEqual(main(["--port", "9000"]), "serving on 127.0.0.1:9000")


if __name__ == "__main__":
    unittest.main()
PY
    cat > "$1/README.md" <<'MD'
# app

Start it with `python3 -m app.cli --port 8080`. The `port` setting defaults to 8080.
MD
}

prepare() {   # prepare <type>: a fresh copy of that fixture, printed as a path
    local dir="$FIXTURES/$1"
    rm -rf "$dir"
    mkdir -p "$dir"
    case "$1" in
        webapp) echo '{"name": "demo", "private": true, "dependencies": {"next": "14.2.3", "react": "18.3.1", "react-dom": "18.3.1"}}' > "$dir/package.json" ;;
        edit) printf 'server:\n  host: 127.0.0.1\n  port: 8080\n  workers: 2\nlogging:\n  level: info\n' > "$dir/config.yml" ;;
        repo) git -C "$REPO_DIR" archive HEAD | tar -x -C "$dir" ;;
        app) make_app "$dir" ;;
    esac
    echo "$dir"
}

# llama-server busy with another client (another Pi session, an Open WebUI chat)? Pi's sessions share its slot, so a
# test then waits for every step and runs into its time limit (run 17: B3, C1 and D2 while a Pi session worked).
LLAMA_AUTH=()
[[ -f "$LLAMA_API_KEY_FILE" ]] && LLAMA_AUTH=(-H "Authorization: Bearer $(<"$LLAMA_API_KEY_FILE")")
llama_busy() {
    curl -fsS -m 3 "${LLAMA_AUTH[@]}" http://127.0.0.1:8081/slots 2>/dev/null |
        python3 -c 'import json,sys; sys.exit(0 if any(s.get("is_processing") for s in json.load(sys.stdin)) else 1)' 2>/dev/null
}
MAX_IDLE_WAIT=1800
wait_for_idle_model() {   # before each test: until no slot is working, at most MAX_IDLE_WAIT seconds
    local waited=0
    while llama_busy; do
        (( waited == 0 )) && echo "    llama-server is busy with another client (a Pi session or an Open WebUI chat): waiting..."
        if (( waited >= MAX_IDLE_WAIT )); then
            warn "llama-server still busy after $((MAX_IDLE_WAIT / 60)) minutes: starting anyway (this test will be slow)."
            return
        fi
        sleep 5
        waited=$((waited + 5))
    done
    (( waited > 0 )) && echo "    ...free after ${waited} s."
}
# Checked before the first test, when no Pi of this script runs yet.
other_pi="$(pgrep -x pi | tr '\n' ' ')"
if [[ -n "$other_pi" ]]; then
    warn "Another Pi session is open (pid ${other_pi% }). If it works during the run, it shares the model with the tests and slows them down; each test waits until the model is free before it starts."
fi

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
