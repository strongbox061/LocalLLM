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
command -v pi >/dev/null || die "Pi isn't installed: install/pi.sh."

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
export PI_CONTEXT7_ID_CACHE="$WORK/context7-ids.json"

PROMPT="Use Context7: in Next.js 14, how does a page component read a dynamic route parameter such as [slug]? \
Find the library ID with resolve-library-id, then look it up in the v14 docs with query-docs. Answer in two sentences."

echo "Asking Pi (Context7 tools only; about a minute)..."
START="$(date +%s)"
(cd "$WORK" && timeout 600 pi -p "$PROMPT" --tools 'context7_*' </dev/null >"$WORK/answer.txt" 2>"$WORK/errors.txt")
status=$?
[[ $status -eq 0 ]] || echo "Pi exited with status $status: $(tail -3 "$WORK/errors.txt")"

# The session of this run: the newest one whose working folder is $WORK.
SESSION="$(python3 - "$PI_AGENT_DIR/sessions" "$WORK" "$START" <<'EOF'
import json, os, sys
root, cwd, start = sys.argv[1], sys.argv[2], float(sys.argv[3])
best = None
for d, _, files in os.walk(root):
    for f in files:
        p = os.path.join(d, f)
        if not f.endswith(".jsonl") or os.path.getmtime(p) < start - 5:
            continue
        try:
            first = json.loads(open(p).readline())
        except Exception:
            continue
        if first.get("cwd") == cwd and (best is None or os.path.getmtime(p) > os.path.getmtime(best)):
            best = p
print(best or "")
EOF
)"
[[ -n "$SESSION" ]] || die "No Pi session file for this run under $PI_AGENT_DIR/sessions."

python3 - "$SESSION" <<'EOF'
import json, sys
calls, results = [], []
for line in open(sys.argv[1]):
    d = json.loads(line)
    if d.get("type") != "message":
        continue
    m = d["message"]
    if m.get("role") == "assistant":
        for p in m.get("content") or []:
            if p.get("type") == "toolCall":
                calls.append((p.get("name"), p.get("arguments")))
    elif m.get("role") == "toolResult":
        text = " ".join(p.get("text", "") for p in m.get("content") or [] if p.get("type") == "text")
        results.append((m.get("toolName"), bool(m.get("isError")), text))

for name, args in calls:
    print(f"  call    {name} {json.dumps(args, ensure_ascii=False)[:110]}")
# A result counts when it isn't an error and didn't come from Pi's own guard ("Not sent to Context7 ...").
def failed(err, text):
    return err or text.lower().startswith("not sent to context7")
def good(name):
    return [t for n, err, t in results if n == name and not failed(err, t)]
resolved, docs = good("context7_resolve-library-id"), good("context7_query-docs")
problems = [t[:160].replace("\n", " ") for n, err, t in results if n and n.startswith("context7_") and failed(err, t)]
for t in problems:
    print(f"  error   {t}")
ok = True
# Verify we got exactly two tool calls (resolve-library-id + query-docs).
if len(calls) != 2:
    print(f"  FAIL    expected 2 tool calls, got {len(calls)}"); ok = False
if resolved:
    ids = sorted({w for t in resolved for w in t.split() if w.startswith("/") and w.count("/") >= 2})
    print(f"  ok      resolve-library-id answered ({', '.join(ids[:3]) or 'IDs listed'})")
else:
    print("  FAIL    resolve-library-id gave no result"); ok = False
if docs:
    print(f"  ok      query-docs returned docs ({len(docs[0])} characters)")
else:
    print("  FAIL    query-docs gave no docs"); ok = False
sys.exit(0 if ok else 1)
EOF
result=$?

echo "  answer  $(tr '\n' ' ' <"$WORK/answer.txt" | cut -c1-300)"
echo "  session ${SESSION/#$HOME/\~}"
if [[ $result -eq 0 ]]; then
    echo "Context7: ready (signed in, docs lookup works)."
else
    echo "Context7: not working. If an error above mentions a sign-in, run /mcp-auth context7 in an interactive Pi" \
         "session; then run this check again."
fi
exit $result
