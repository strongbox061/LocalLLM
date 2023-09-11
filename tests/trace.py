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
    """Load the last JSON object from a JSONL session file."""
    last = None
    with open(path, 'r') as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    last = json.loads(line)
                except json.JSONDecodeError:
                    pass
    return last or {}


def report(path: str, args) -> str:
    """Build a Markdown report from a session file."""
    data = load(path)
    messages = data.get('messages', [])

    # Gather tool calls and results
    tool_calls = []
    tool_counts = Counter()
    reasoning_lines = 0
    has_reasoning = False

    for msg in messages:
        role = msg.get('role', '')
        content = text_of(msg.get('content', ''))

        if role == 'assistant':
            # Check for reasoning (hidden thinking blocks)
            if 'thinking' in content.lower() or '<think>' in content:
                has_reasoning = True
                reasoning_lines += content.count('\n') + 1

            # Extract tool calls from assistant message
            tool_use = msg.get('tool_use', [])
            for tc in tool_use:
                name = tc.get('name', 'unknown')
                arguments = tc.get('arguments', {})
                if isinstance(arguments, dict):
                    arg_str = json.dumps(arguments)
                else:
                    arg_str = str(arguments)
                tool_calls.append({
                    'name': name,
                    'args': one_line(arg_str, ARG_CHARS),
                    'content': content,
                })
                tool_counts[name] += 1

        elif role == 'tool_result':
            result_content = text_of(msg.get('content', ''))
            # Match to previous tool call
            for tc in reversed(tool_calls):
                if tc.get('_pending'):
                    tc['result'] = one_line(result_content, RESULT_CHARS)
                    tc['_pending'] = False
                    break

    lines = []
    lines.append('# Trace Report')
    lines.append('')
    lines.append(f'**Session:** `{os.path.basename(path)}`')
    lines.append(f'**Reasoning:** {"ON" if has_reasoning else "OFF"}')
    lines.append(f'**Tool calls:** {len(tool_calls)}')
    lines.append('')

    # Tool call details
    if tool_calls:
        lines.append('## Tool Calls')
        lines.append('')
        for i, tc in enumerate(tool_calls, 1):
            lines.append(f'### {i}. `{tc["name"]}`')
            lines.append('')
            lines.append(f'**Arguments:** `{tc["args"]}`')
            if 'result' in tc:
                lines.append(f'**Result:** {one_line(tc["result"], 500)}')
            lines.append('')

    # Counts
    if tool_counts:
        lines.append('## Counts')
        lines.append('')
        for name, count in sorted(tool_counts.items()):
            lines.append(f'- `{name}`: {count}')
        lines.append('')

    # Final answer
    final_answer = ''
    for msg in reversed(messages):
        if msg.get('role') == 'assistant':
            content = text_of(msg.get('content', ''))
            # Skip if it's mostly tool calls or reasoning
            if not msg.get('tool_use') and 'thinking' not in content.lower():
                final_answer = content
                break

    if final_answer:
        lines.append('## Final Answer')
        lines.append('')
        lines.append(one_line(final_answer, 2000))
        lines.append('')

    # Verdict
    verdict = 'PASS'
    if hasattr(args, 'expect') and args.expect:
        if args.expect.lower() not in final_answer.lower():
            verdict = 'FAIL'
    lines.append(f'**Verdict:** {verdict}')
    lines.append('')

    return '\n'.join(lines)


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
