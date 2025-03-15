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
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok    {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}" + (f"\n        {detail}" if detail else ""))


if importlib.util.find_spec("pydantic") is None:
    pydantic = types.ModuleType("pydantic")
    pydantic.BaseModel = type("BaseModel", (), {})
    pydantic.Field = lambda default=None, **_: default
    sys.modules["pydantic"] = pydantic

os.environ.pop("CONTEXT_COMPACTION_TOKEN_THRESHOLD", None)
spec = importlib.util.spec_from_file_location("old_pages", ROOT / "open-webui" / "functions" / "old_pages.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

_n = 0


def call(name, **args):
    global _n
    _n += 1
    cid = f"call_{_n}"
    return cid, {"role": "assistant", "content": "", "reasoning_content": "thinking",
                 "tool_calls": [{"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def answer(question, pages, searches=1, page_chars=20000):
    """One question and its answer: searches, page reads, the final text."""
    out = [{"role": "user", "content": question}]
    for i in range(searches):
        cid, m = call("web_search", query=f"{question} {i}")
        out += [m, {"role": "tool", "tool_call_id": cid, "content": "1. Result\n   https://r.example\n   " + "s" * 1400}]
    for url in pages:
        cid, m = call("read_page", url=url)
        out += [m, {"role": "tool", "tool_call_id": cid, "content": f"{url}\n" + "p" * page_chars}]
    out.append({"role": "assistant", "content": f"Answer to {question}, from {', '.join(pages)}."})
    return out


def run(messages, **valves):
    f = mod.Filter()
    for k, v in valves.items():
        setattr(f.valves, k, v)
    body = {"model": "local", "messages": copy.deepcopy(messages)}
    return f.request(body)["messages"]


SYSTEM = [{"role": "system", "content": "x" * 30000}]  # not counted: the same for every chat

# A short chat: one answer with two small pages, then a follow-up.
short = SYSTEM + answer("first", ["https://a.example/1"], page_chars=3000) + [{"role": "user", "content": "more?"}]
check("a short chat goes to the model unchanged", run(short) == short)

# A long chat: two answers with big pages, and a third being written (with its own page already read).
first = answer("first", ["https://a.example/1", "https://a.example/2"])
second = answer("second", ["https://b.example/1", "https://b.example/2"])
current = answer("third", ["https://c.example/1"])[:-1]  # still being written: no final text yet
long = SYSTEM + first + second + current
out = run(long)
tools_before = [m for m in out[: len(SYSTEM) + len(first) + len(second)] if m["role"] == "tool"]
pages_before = [m for m in tools_before if m["content"].startswith(mod.NOTE_START)]
check("pages of earlier answers become notes (4 of 4)", len(pages_before) == 4, str([m["content"][:60] for m in tools_before]))
check("a note keeps the page's address and says how to read it again",
      "https://a.example/1" in pages_before[0]["content"] and "read_page" in pages_before[0]["content"]
      and "20,0" in pages_before[0]["content"], pages_before[0]["content"])
check("the answer being written keeps its page in full", out[-1]["content"] == current[-1]["content"])
check("search results stay", all(m["content"].startswith("1. Result") for m in tools_before if m not in pages_before))
check("your messages and the answers stay as they are",
      [m for m in out if m["role"] in ("user", "system") or (m["role"] == "assistant")] ==
      [m for m in long if m["role"] in ("user", "system") or (m["role"] == "assistant")])
before = sum(len(m["content"]) for m in long if m["role"] == "tool") // 4
after = sum(len(m["content"]) for m in out if m["role"] == "tool") // 4
check(f"the chat shrinks (tool text ~{before} -> ~{after} tokens)", after < before / 3)
check("running it twice changes nothing more", run(out) == out)
check("the stored chat isn't touched (only the request's copy)", long[len(SYSTEM) + 4]["content"].startswith("https://a.example/1\n"))

# A later part of a long page: the note says which part to ask for again.
cid, m = call("read_page", url="https://long.example/doc#install", part=3)
parted = SYSTEM + first + second + [{"role": "user", "content": "q"}, m, {"role": "tool", "tool_call_id": cid, "content": "z" * 20000},
                                    {"role": "assistant", "content": "a"}, {"role": "user", "content": "next"}]
note = [x["content"] for x in run(parted) if x["role"] == "tool" and "long.example" in x["content"]]
check("a later part's note keeps its #section and part", note and "doc#install" in note[0] and "part=3" in note[0], note)

# Below the starting point, nothing changes; the starting point is counted with the pages in full.
check("nothing changes below start_at_tokens", run(long, start_at_tokens=10**6) == long)
check("short pages stay", all(not m["content"].startswith(mod.NOTE_START)
                               for m in run(long, min_chars=10**6) if m["role"] == "tool"))

# Other tools' results (e.g. the protected chats' lookup) stay.
cid, m = call("protected_lookup", query="norma")
other = SYSTEM + [{"role": "user", "content": "q"}, m, {"role": "tool", "tool_call_id": cid, "content": "q" * 90000},
                  {"role": "assistant", "content": "a"}, {"role": "user", "content": "next"}]
check("other tools' results stay", run(other) == other)

# Open WebUI's own message after tool images isn't the user's question: the page before it is the current answer's.
cid, m = call("read_page", url="https://img.example")
images = (SYSTEM + first + second + [{"role": "user", "content": "third"}, m,
                                      {"role": "tool", "tool_call_id": cid, "content": "i" * 20000},
                                      {"role": "user", "content": [{"type": "text", "text": mod.IMAGE_NOTE + " Please analyze them."}]}])
out = run(images)
check("the image message isn't taken for a new question", out[-2]["content"] == "i" * 20000)
check("no question at all: unchanged", run(SYSTEM) == SYSTEM)

# The starting point follows Open WebUI's compaction threshold (services/open-webui.sh sets it).
f = mod.Filter()
check("default start without the threshold: 20,000 tokens", f._start_tokens() == 20000)
os.environ["CONTEXT_COMPACTION_TOKEN_THRESHOLD"] = "50176"
check("start at 40% of the compaction threshold (50,176 -> 20,070)", f._start_tokens() == 20070)
f.valves.start_at_tokens = 12345
check("start_at_tokens wins when set", f._start_tokens() == 12345)

print(f"old-pages-check.py: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
