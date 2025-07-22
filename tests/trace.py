#!/usr/bin/env python3
"""Turns one Pi session file (JSONL) into a Markdown report for judging a test run.

    tests/trace.py <session.jsonl> [--id ID --prompt TEXT --expect TEXT --seconds N]

The report shows what the run actually did: reasoning on or off, every tool call with the arguments the model sent, what
each call returned (or why it was blocked), counts per tool, and the final answer. Verdicts come from this trace, not
from the answer alone. With --summary, prints one tab-separated summary line instead (used by run-pi-tests.sh).

    tests/trace.py --follow <sessions dir> --since <marker file> --pid <pi's pid>

prints each tool call and a short result line as Pi writes them (run-pi-tests.sh uses it for its live output), and
stops when that Pi process has exited.
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
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Session file not found: {path}")
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
                                    "Context7's limit", "You were given a URL", "The old text")):
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
    run = load(path)
    tools = ", ".join(f"{n}×{c}" for n, c in run["tools"].most_common()) or "none"
    return "\t".join([args.id or "?", str(args.seconds or "?"), "on" if run["thinking"] else "off", tools, str(run["errors"])])


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def follow(sessions: str, marker: str, pid: int) -> int:
    """Live view of one run: waits for its session file, then prints calls and results as they are written."""
    since = os.path.getmtime(marker)
    path = None
    while path is None:
        found = [f for f in glob.glob(os.path.join(sessions, "*", "*.jsonl")) if os.path.getmtime(f) >= since]
        if found:
            path = max(found, key=os.path.getmtime)
        elif not alive(pid):
            return 0
        else:
            time.sleep(0.5)
    position, pending = 0, ""
    while True:
        running = alive(pid)
        with open(path, encoding="utf-8") as f:
            f.seek(position)
            pending += f.read()
            position = f.tell()
        *lines, pending = pending.split("\n")
        for line in lines:
            try:
                message = json.loads(line).get("message") or {}
            except ValueError:
                continue
            role, content = message.get("role"), message.get("content")
            if role == "assistant" and isinstance(content, list):
                for part in content:
                    if part.get("type") == "toolCall":
                        args = json.dumps(part.get("arguments"), ensure_ascii=False)
                        print(f"    → {part.get('name')} {one_line(args, 160)}", flush=True)
            elif role == "toolResult":
                mark = "✗" if message.get("isError") else "✓"
                print(f"      {mark} {one_line(text_of(content), 140)}", flush=True)
        if not running:
            return 0
        time.sleep(0.5)


def main() -> int:
    if "--follow" in sys.argv:
        live = argparse.ArgumentParser()
        live.add_argument("--follow", required=True)
        live.add_argument("--since", required=True)
        live.add_argument("--pid", type=int, required=True)
        a = live.parse_args()
        return follow(a.follow, a.since, a.pid)
    parser = argparse.ArgumentParser()
    parser.add_argument("session")
    parser.add_argument("--id", default="")
    parser.add_argument("--prompt", default="")
    parser.add_argument("--expect", default="")
    parser.add_argument("--seconds", default="")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    print(summary(args.session, args) if args.summary else report(args.session, args))
    return 0


if __name__ == '__main__':
    sys.exit(main())
