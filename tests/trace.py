#!/usr/bin/env python3
"""Turns one Pi session file (JSONL) into a Markdown report for judging a test run.

    tests/trace.py <session.jsonl> [--id ID --prompt TEXT --expect TEXT --seconds N]

The report shows what the run actually did: reasoning on or off, every tool call with the arguments the model sent, what
each call returned (or why it was blocked), counts per tool, and the final answer. Verdicts come from this trace, not
from the answer alone. With --summary, prints one tab-separated summary line instead (used by run-pi-tests.sh).

    tests/trace.py --follow <sessions dir> --since <marker file> --pid <pi's pid>

prints each tool call and a short result line as Pi writes them (run-pi-tests.sh uses it for its live output), and
prints a final report when the session ends.
"""

import argparse
import glob
import json
import os
import sys
import time
from collections import Counter

ARG_CHARS = 400
RESULT_CHARS = 300


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""


def one_line(text: str, limit: int) -> str:
    text = " | ".join(line.strip() for line in str(text).splitlines() if line.strip())
    return text if len(text) <= limit else text[:limit] + " …"


def load(path: str) -> dict:
    run = {"steps": [], "thinking": 0, "answer": "", "tools": Counter(), "blocked": 0, "errors": 0}
    for line in open(path, encoding="utf-8"):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        message = entry.get("message") or {}
        role, content = message.get("role"), message.get("content")
        if role == "assistant" and isinstance(content, list):
            for part in content:
                if part.get("type") == "thinking" and (part.get("thinking") or "").strip():
                    run["thinking"] += 1
                elif part.get("type") == "toolCall":
                    run["tools"][part.get("name", "?")] += 1
                    run["steps"].append(("call", part.get("name", "?"), json.dumps(part.get("arguments"), ensure_ascii=False)))
            answer = text_of(content).strip()
            if answer:
                run["answer"] = answer
        elif role == "toolResult":
            text = text_of(content)
            error = bool(message.get("isError"))
            if error:
                run["errors"] += 1
                if text.startswith(("Not sent to", "You already", "Don't search", "You've used enough", "Search budget",
                    "You have reached your daily limit for", "Rate limit exceeded", "Too many requests", "Quota exceeded")):
                    run["blocked"] += 1
            run["steps"].append(("result", message.get("toolName", "?"), ("ERROR: " if error else "") + text))
    return run


def report(path: str, args) -> str:
    run = load(path)
    lines = [f"# {args.id or 'Test run'}", ""]
    if args.prompt:
        lines += [f"**Prompt:** {args.prompt}", ""]
    if args.expect:
        lines += [f"**Expected:** {args.expect}", ""]
    lines += [
        f"**Session:** `{path}`",
        f"**Time:** {args.seconds or '?'} s · **Reasoning:** {'on' if run['thinking'] else 'off'} "
        f"· **Tool calls:** {sum(run['tools'].values())} "
        f"({', '.join(f'{n} ×{c}' for n, c in run['tools'].most_common()) or 'none'}) "
        f"· **Errors/blocks:** {run['errors']}",
        "",
        "## Trace",
        "",
    ]
    for kind, name, text in run["steps"]:
        if kind == "call":
            lines.append(f"- **{name}** `{one_line(text, ARG_CHARS)}`")
        else:
            lines.append(f"  - → {one_line(text, RESULT_CHARS)}")
    if not run["steps"]:
        lines.append("(no tool calls)")
    lines += ["", "## Answer", "", run["answer"] or "(no answer)", "", "## Verdict", "", "- [ ] pass  - [ ] fail  — notes:", ""]
    return "\n".join(lines)


def summary(path: str, args) -> str:
    """Print one TSV summary line for automated testing."""
    data = load(path)
    messages = data.get('messages', [])

    tool_counts = Counter()
    has_reasoning = False
    final_answer = ''

    for msg in messages:
        role = msg.get('role', '')
        content = text_of(msg.get('content', ''))

        if role == 'assistant':
            if 'thinking' in content.lower():
                has_reasoning = True
            tool_use = msg.get('tool_use', [])
            for tc in tool_use:
                tool_counts[tc.get('name', 'unknown')] += 1
            if not tool_use and 'thinking' not in content.lower():
                final_answer = content

    verdict = 'PASS'
    if hasattr(args, 'expect') and args.expect:
        if args.expect.lower() not in final_answer.lower():
            verdict = 'FAIL'

    return '\t'.join([
        os.path.basename(path),
        str(len(tool_counts)),
        'yes' if has_reasoning else 'no',
        verdict,
    ])


def alive(pid: int) -> bool:
    """Check if a process with the given PID is still running."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def follow(sessions: str, marker: str, pid: int) -> int:
    """Follow a sessions directory and print tool calls as they appear.

    Returns the number of sessions processed.
    """
    if not alive(pid):
        print(f'Process {pid} is no longer running', file=sys.stderr)
        return 0

    marker_data = {}
    if os.path.exists(marker):
        with open(marker, 'r') as fh:
            try:
                marker_data = json.load(fh)
            except json.JSONDecodeError:
                pass

    count = 0
    seen_files = set(marker_data.get('files', []))

    while alive(pid):
        files = sorted(glob.glob(os.path.join(sessions, '*.jsonl')))
        for fpath in files:
            if fpath in seen_files:
                continue
            seen_files.add(fpath)

            # Read new lines from the file
            try:
                with open(fpath, 'r') as fh:
                    fh.seek(marker_data.get(fpath, 0))
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                            messages = obj.get('messages', [])
                            for msg in messages:
                                if msg.get('role') == 'assistant':
                                    tool_use = msg.get('tool_use', [])
                                    for tc in tool_use:
                                        name = tc.get('name', '')
                                        args_str = json.dumps(tc.get('arguments', {}))
                                        print(f'[TOOL] {name}({args_str})')
                        except (json.JSONDecodeError, KeyError):
                            pass

                    marker_data[fpath] = fh.tell()
            except IOError:
                continue

        # Update marker file
        with open(marker, 'w') as fh:
            json.dump(marker_data, fh)

        count += 1
        time.sleep(0.5)

    return count


def main() -> int:
    parser = argparse.ArgumentParser(description='Trace a Pi session into Markdown or summary.')
    parser.add_argument('session', nargs='?', help='Path to a JSONL session file')
    parser.add_argument('--id', help='Run ID (for report metadata)')
    parser.add_argument('--prompt', help='Prompt text used in the run')
    parser.add_argument('--expect', help='Expected text for verdict check')
    parser.add_argument('--seconds', type=int, help='Timeout in seconds')
    parser.add_argument('--summary', action='store_true', help='Print TSV summary instead of Markdown')
    parser.add_argument('--follow', help='Follow a sessions directory')
    parser.add_argument('--since', help='Marker file for follow mode')
    parser.add_argument('--pid', type=int, help='Pi process PID for follow mode')

    args = parser.parse_args()

    if args.follow:
        if not args.since or not args.pid:
            print('Error: --follow requires --since and --pid', file=sys.stderr)
            return 1
        count = follow(args.follow, args.since, args.pid)
        print(f'Processed {count} scan cycles', file=sys.stderr)
        return 0

    if not args.session:
        parser.print_help()
        return 1

    if not os.path.exists(args.session):
        print(f'File not found: {args.session}', file=sys.stderr)
        return 1

    if args.summary:
        print(summary(args.session, args))
    else:
        print(report(args.session, args))

    return 0


if __name__ == '__main__':
    sys.exit(main())
