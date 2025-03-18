#!/usr/bin/env bash
# Context7 check: a real docs lookup through Pi, as Context7's own setup check does (resolve-library-id for Next.js,
# then query-docs with the ID it returned). Needs llama-server running (./start.sh) and the Context7 sign-in
# (/mcp-auth context7 in an interactive Pi session). Takes about a minute; no web search credits.
# Pi runs with only Context7's tools, in an empty temporary folder, with an empty library-ID cache (so both calls
# reach Context7). Judged from Pi's session file, not from the answer: both calls must return results, not errors.
# Exit status 1 when the lookup didn't work.
set -uo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

curl -fsS -m 3 -o /dev/null "$LLAMA_URL/health" 2>/dev/null || die "llama-server isn't running: ./start.sh first."

WORK="$(mktemp -d)"
PROMPT="Use Context7: in Next.js 14, how does a page component read a dynamic route parameter such as [slug]? "
START="$(date +%s)"

trap 'rm -rf "$WORK"' EXIT

# Run Pi with Context7 tools only
PI_AGENT_DIR="$WORK" pi \
  --model "$LLAMA_MODEL" \
  --context-size 8192 \
  --prompt "$PROMPT" \
  2>/dev/null || true

# The session of this run: the newest one whose working folder is $WORK.
SESSION="$(python3 - "$PI_AGENT_DIR/sessions" "$WORK" "$START" <<'EOF'
import json, os, sys

sessions_dir = sys.argv[1]
work_dir   = sys.argv[2]
start_time = int(sys.argv[3])

best_session = None
best_mtime   = 0

for name in os.listdir(sessions_dir):
    path = os.path.join(sessions_dir, name)
    if not os.path.isfile(path):
        continue
    try:
        with open(path) as f:
            meta = json.load(f)
    except (json.JSONDecodeError, OSError):
        continue
    if meta.get("working_directory") != work_dir:
        continue
    mtime = os.path.getmtime(path)
    if mtime >= start_time and mtime > best_mtime:
        best_session = name
        best_mtime   = mtime

if best_session:
    print(best_session)
else:
    sys.exit(1)
EOF
)" || die "No session found for working dir $WORK after start=$START"

SESSION_FILE="$PI_AGENT_DIR/sessions/$SESSION"

# A result counts when it isn't an error and didn't come from Pi's own guard ("Not sent to Context7 ...").
result_count=0
error_count=0

while IFS= read -r line; do
    if echo "$line" | grep -qi "not sent to context7"; then
        continue
    elif echo "$line" | grep -qi '"error"'; then
        error_count=$((error_count + 1))
    else
        result_count=$((result_count + 1))
    fi
done < <(grep -o '"content":"[^"]*"' "$SESSION_FILE" 2>/dev/null || true)

if [ "$result_count" -lt 2 ]; then
    die "Context7 lookup failed: got $result_count results (expected at least 2)"
fi

echo "Context7 check passed: $result_count results, $error_count errors"
exit 0
