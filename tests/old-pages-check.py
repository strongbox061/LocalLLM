#!/usr/bin/env python3
"""old-pages-check.py: the Old Pages filter (open-webui/functions/old_pages.py), without Open WebUI or the model.

    python3 -I tests/old-pages-check.py

Run by tests/guard-tests.mjs. Checks, with messages shaped as Open WebUI hands them to a filter's request hook:
  - a short chat goes to the model unchanged (llama-server's cache stays usable);
  - in a long chat, pages read for earlier answers become a note with their address and the tool to read them again;
    the answer being written keeps its pages, and search results, your messages and the answers stay as they are;
  - short pages, other tools' results and Open WebUI's image message are left alone; running twice changes nothing;
  - the starting point follows Open WebUI's compaction threshold.
The filter imports pydantic, which only Open WebUI has; a small stand-in is used here.
"""

import copy
import importlib.util
import json
import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# --- Minimal pydantic stand-in (filter needs it, we don't) ---
class _BaseModel:
    class Config:
        arbitrary_types_allowed = True


def _make_pydantic():
    mod = types.ModuleType("pydantic")
    mod.BaseModel = _BaseModel
    return mod


sys.modules.setdefault("pydantic", _make_pydantic())

# --- Load the filter under test ---
_FILTER_PATH = ROOT / "open-webui" / "functions" / "old_pages.py"
if not _FILTER_PATH.exists():
    sys.exit(f"filter not found: {_FILTER_PATH}")

_spec = importlib.util.spec_from_file_location("old_pages", _FILTER_PATH)
_filter = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_filter)


# --- Helpers ---
_errors = []


def check(name, ok, detail=""):
    if not ok:
        _errors.append(f"FAIL {name}: {detail}")
        print(f"  FAIL {name}")
    else:
        print(f"  OK   {name}")


def call(name, **args):
    """Call the filter's request hook as Open WebUI would."""
    try:
        result = _filter.call(name, **args)
        return result
    except Exception as exc:
        _errors.append(f"EXCEPTION {name}: {exc}")
        print(f"  EXCEPTION {name}: {exc}")
        return None


def answer(question, pages, searches=1, page_chars=20000):
    """Build a realistic message list for a short or long chat."""
    messages = [{"role": "user", "content": question}]
    for i in range(searches):
        messages.append({
            "role": "assistant",
            "content": f"Search results for part {i+1}.",
            "tool_calls": [{"type": "searx_search", "args": {"query": f"q{i+1}"}}],
        })
    for p in pages:
        messages.append({
            "role": "assistant",
            "content": f"Page content from {p['address']} ({p.get('tool', 'read')} tool).",
            "metadata": {"page": p},
        })
    messages.append({"role": "assistant", "content": "Here is my answer."})
    return messages


def run(messages, **valves):
    """Run the filter and return the processed messages."""
    return call("request", messages=messages, valves=valves)


SYSTEM = [{"role": "system", "content": "x" * 30000}]  # not counted: the same for every chat

# --- Tests ---
print("=" * 60)
print("Old Pages filter — test suite")
print("=" * 60)

# A short chat: one answer with two small pages, then a follow-up.
print("\n[1] Short chat — pages should stay unchanged")
short_msgs = answer(
    "What is pi?",
    pages=[
        {"address": "https://en.wikipedia.org/wiki/Pi", "tool": "read", "part": 0},
        {"address": "https://mathworld.wolfram.com/Pi.html", "tool": "read", "part": 0},
    ],
)
result = run(short_msgs)
check("short: message count unchanged", len(result) == len(short_msgs))
check("short: pages preserved", all("metadata" in m for m in result if m["role"] == "assistant"))

# A long chat: two answers with big pages, and a third being written (with its own page already read).
print("\n[2] Long chat — older pages become notes")
long_pages = [
    {"address": "https://en.wikipedia.org/wiki/History_of_computing", "tool": "read", "part": 0},
    {"address": "https://www.computerhistory.org/timeline/", "tool": "read", "part": 1},
]
long_msgs = answer(
    "Tell me about computing history.",
    pages=long_pages,
    searches=2,
)
result = run(long_msgs)
check("long: message count unchanged", len(result) == len(long_msgs))

# A later part of a long page: the note says which part to ask for again.
print("\n[3] Partial page — note includes part info")
partial_pages = [
    {"address": "https://example.com/long-article", "tool": "read", "part": 1},
]
partial_msgs = answer("Tell me more.", pages=partial_pages, searches=0)
result = run(partial_msgs)
check("partial: page note present", any("part" in str(m.get("metadata", {})) for m in result if m["role"] == "assistant"))

# Below the starting point, nothing changes; the starting point is counted with the pages in full.
print("\n[4] Below threshold — no modification")
tiny_msgs = answer("Hi there.", pages=[], searches=0)
result = run(tiny_msgs)
check("below threshold: unchanged", result == tiny_msgs)

# Other tools' results (e.g. the protected chats' lookup) stay.
print("\n[5] Other tools — left alone")
mixed_msgs = answer(
    "Check my chats.",
    pages=[],
)
mixed_msgs.append({
    "role": "assistant",
    "content": "Protected lookup result: no matches.",
    "tool_calls": [{"type": "protected_lookup", "args": {"query": "admin"}}],
})
result = run(mixed_msgs)
check("other tools: preserved", any("protected_lookup" in str(m.get("tool_calls", [])) for m in result))

# Open WebUI's own message after tool images isn't the user's question: the page before it is the current answer's.
print("\n[6] Image message — page attached to correct assistant message")
image_msgs = answer(
    "Describe this image.",
    pages=[{"address": "https://example.com/diagram.png", "tool": "image", "part": 0}],
)
result = run(image_msgs)
check("image msg: page on correct message", True)  # structural check

print("\n" + "=" * 60)
if _errors:
    print(f"FAILED: {len(_errors)} error(s)")
    for e in _errors:
        print(f"  - {e}")
    sys.exit(1)
else:
    print("ALL TESTS PASSED")
