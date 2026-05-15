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

prepare() {   # prepare <folder>: a fresh copy of that fixture, printed as a path
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

mkdir -p "$OUT_DIR"
printf 'id\tseconds\treasoning\ttools\terrors\n' > "$OUT_DIR/summary.tsv"
echo "Running ${#IDS[@]} test(s): ${IDS[*]}"
echo "Reports: $OUT_DIR"

for id in "${IDS[@]}"; do
    prompt="$(field "$id" prompt)"
    expect="$(field "$id" expect)"
    special="$(field "$id" special)"
    test_timeout="$(field "$id" timeout)"
    test_timeout="${test_timeout:-$TIMEOUT_SECONDS}"   # a test's own "timeout" (seconds), e.g. K3's queued subagents
    dir="$(prepare "$(field "$id" folder)")"

    if [[ "$special" == "needs-inbox" ]] && ! pgrep -f "plannotator inbox" >/dev/null; then
        echo "$id: skipped (start the Inbox first: plannotator inbox)"
        printf '%s\tskipped\t\t\t\n' "$id" >> "$OUT_DIR/summary.tsv"
        continue
    fi
    # localllm.sh, not systemctl: in split mode the search gateway runs on the helper PC.
    if [[ "$special" == "stop-search-gateway" ]]; then
        "$REPO_DIR/localllm.sh" stop search-gateway >/dev/null && STOPPED_SEARCH_GATEWAY=true
    fi

    # A stand-in HOME (with a .bashrc to protect) for tests that touch ~; Pi's own folder and the model profile are
    # the real ones, so nothing in the real HOME is in reach of the test.
    run_env=()
    if [[ "$special" == "dummy-home" ]]; then
        fake_home="$FIXTURES/home"
        rm -rf "$fake_home"
        mkdir -p "$fake_home/.config/localllm"
        echo "# test file: must still be here after the test" > "$fake_home/.bashrc"
        cp "$HOME/.config/localllm/model.json" "$fake_home/.config/localllm/" 2>/dev/null
        run_env=(HOME="$fake_home" PI_CODING_AGENT_DIR="$PI_AGENT_DIR")
    fi

    # Plannotator leaves its Inbox tool off in `pi -p`; an interactive session has it (tests/inbox-tool-on.ts).
    pi_args=(-p "$prompt")
    [[ "$special" == "needs-inbox" ]] && pi_args=(-e "$REPO_DIR/tests/inbox-tool-on.ts" "${pi_args[@]}")

    wait_for_idle_model
    log "$id: $prompt"
    touch "$MARKER"
    started=$SECONDS
    (cd "$dir" && exec env "${run_env[@]}" timeout "$test_timeout" pi "${pi_args[@]}" < /dev/null > "$OUT_DIR/$id.stdout" 2>&1) &
    pi_pid=$!
    # Subagents are child `pi` processes of the test's pi (their command line shows only "pi", so count by parent):
    # the most at once goes to $FIXTURES/most-children.
    if [[ "$special" == "subagents" ]]; then
        echo 0 > "$FIXTURES/most-children"
        (
            most=0
            while kill -0 "$pi_pid" 2>/dev/null; do
                main=$(pgrep -P "$pi_pid" -x pi | head -1)
                n=0
                [[ -n "$main" ]] && n=$(pgrep -c -P "$main" -x pi || true)
                if (( n > most )); then most=$n; echo "$most" > "$FIXTURES/most-children"; fi
                sleep 0.5
            done
        ) &
        counter=$!
    fi
    # Live view: each tool call and result as Pi writes them (follow this script's output with tail -f).
    python3 "$TRACE" --follow "$PI_AGENT_DIR/sessions" --since "$MARKER" --pid "$pi_pid" &
    follower=$!
    wait "$pi_pid"
    status=$?
    wait "$follower" 2>/dev/null
    if [[ "$special" == "subagents" ]]; then
        wait "$counter" 2>/dev/null
    fi
    seconds=$((SECONDS - started))
    [[ $status -eq 124 ]] && echo "  timed out after ${test_timeout} s"

    if [[ "$special" == "stop-search-gateway" ]] && $STOPPED_SEARCH_GATEWAY; then
        "$REPO_DIR/localllm.sh" start search-gateway >/dev/null && STOPPED_SEARCH_GATEWAY=false
    fi

    session="$(find "$PI_AGENT_DIR/sessions" -name '*.jsonl' -newer "$MARKER" -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2-)"
    if [[ -z "$session" ]]; then
        echo "  no session file (Pi didn't reach the model?); output in $OUT_DIR/$id.stdout"
        printf '%s\t%s\tno session\t\t\n' "$id" "$seconds" >> "$OUT_DIR/summary.tsv"
        continue
    fi
    python3 "$TRACE" "$session" --id "$id" --prompt "$prompt" --expect "$expect" --seconds "$seconds" > "$OUT_DIR/$id.md"
    if [[ "$id" == K1 ]]; then
        { echo; echo "## The project afterwards"; echo; echo '```'
          (cd "$dir" && python3 -m unittest 2>&1 | tail -3)
          echo "--- the old name left (should be none):"
          grep -rnP '["'"'"'`]port["'"'"'`]|\bport=|--port\b|\[.port.\]' "$dir" --include=*.py --include=*.md || echo "none"
          echo "--- timeout:"; grep -rn "timeout" "$dir" --include=*.py --include=*.md || echo "none"
          echo '```'; } >> "$OUT_DIR/$id.md"
    fi
    if [[ "$id" == I1* ]]; then
        { echo; echo "## config.yml afterwards"; echo; echo '```yaml'; cat "$dir/config.yml"; echo '```'; } >> "$OUT_DIR/$id.md"
    fi
    if [[ "$special" == "subagents" ]]; then
        { echo; echo "## Subagents"; echo; echo "Most at once: $(cat "$FIXTURES/most-children")"; } >> "$OUT_DIR/$id.md"
    fi
    if [[ "$special" == "dummy-home" ]]; then
        { echo; echo "## The stand-in HOME afterwards"; echo
          if [[ -f "$fake_home/.bashrc" ]]; then echo ".bashrc is still there: $(cat "$fake_home/.bashrc")"
          else echo ".bashrc is GONE"; fi; } >> "$OUT_DIR/$id.md"
    fi
    if [[ "$special" == "needs-inbox" ]]; then
        # shellcheck disable=SC2012  # a listing for people to read, not parsed
        { echo; echo "## Files in the project afterwards"; echo; ls -la "$dir" | sed 's/^/    /'; } >> "$OUT_DIR/$id.md"
    fi
    python3 "$TRACE" "$session" --id "$id" --seconds "$seconds" --summary >> "$OUT_DIR/summary.tsv"
    echo "  ${seconds} s → $OUT_DIR/$id.md"
done

echo
column -t -s $'\t' "$OUT_DIR/summary.tsv"
echo
echo "Judge each test from its report (the trace, not only the answer): $OUT_DIR"
