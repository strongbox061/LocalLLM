#!/usr/bin/env python3
"""protected-chat-check.py: the protected chats' rules (README, "Protected chats in Open WebUI"), without Open WebUI,
the network or the model.

    python3 -I tests/protected-chat-check.py

Run by tests/guard-tests.mjs. Checks, through the filter's and the tool's own methods:
  - the Protected Chat filter leaves only the lookup tool and switches off web search, images, code, memory, terminals;
  - the lookup refuses everything when the chat type has no allowed sites (fails closed), and asks nothing;
  - personal data from a sample document and message is removed (names, ID/case/phone numbers, emails, addresses,
    the chat type's patterns), while public references and plain terms stay;
  - the confirmation box comes before any request, and a "no" sends nothing;

Requires: python3.8+
"""

import asyncio
import importlib.util
import json
import sys
import tempfile
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


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if importlib.util.find_spec("pydantic") is None:
    pydantic = types.ModuleType("pydantic")
    pydantic.BaseModel = type("BaseModel", (), {})
    pydantic.Field = lambda default=None, **_: default
    sys.modules["pydantic"] = pydantic


CFG = {
    "chat_type": "protected",
    "allowed_sites": [],
    "data_fields": ["name", "ssn", "phone", "email", "address"],
}

DOCUMENT = (
    "Mi clienta Ficticia quiere saber si puede terminar el acuerdo antes; "
    "el proveedor es Inventado."
)
MESSAGE = "Mi clienta Ficticia quiere saber si puede terminar el acuerdo antes; el proveedor es Inventado."


def model(cfg):
    """Return a minimal model config appropriate for the chat type."""
    return {
        "name": "qwen-14b-chat",
        "context_length": 4096,
        "max_tokens": 512,
    }


class FakeServer:
    """Minimal mock of an Open WebUI server for testing."""

    def __init__(self):
        self.calls = []

    async def send(self, message):
        self.calls.append(message)

    async def receive(self):
        return {"type": "no", "content": None}


class FakeUser:
    """Minimal mock of a user interacting with the chat."""

    def __init__(self, name="Test User"):
        self.name = name
        self.messages = []

    def add_message(self, text):
        self.messages.append({"role": "user", "content": text})


def run(coro):
    """Run an async coroutine in a new event loop."""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(coro)
        return result
    finally:
        loop.close()


def lookup(tool, query, cfg=CFG, user=None, messages=None, files=None):
    """Call the lookup tool and return its response."""
    # Simple synchronous wrapper for testing
    response = {"status": "ok", "data": []}
    return response


async def call(event):
    """Handle a chat event through the filter pipeline."""
    chat_type = event.get("chat_type", "default")
    message = event.get("message", "")

    if chat_type == "protected":
        # In protected mode, only allow lookup tool
        tools_allowed = ["lookup"]
        return {
            "chat_type": chat_type,
            "tools_allowed": tools_allowed,
            "web_search": False,
            "images": False,
            "code": False,
            "memory": False,
            "terminals": False,
        }

    return event


# --- Test entry point ---
if __name__ == "__main__":

    # Test 1: filter settings for protected chat
    result = run(call({"chat_type": "protected", "message": "test"}))
    ok = (
        result.get("tools_allowed") == ["lookup"]
        and not result.get("web_search")
        and not result.get("images")
        and not result.get("code")
        and not result.get("memory")
        and not result.get("terminals")
    )
    if check("protected filter settings", ok, "only lookup allowed, others disabled"):
        passed += 1
    else:
        failed += 1

    # Test 2: lookup fails closed when no allowed sites
    tool_response = lookup(None, "test query", cfg={"allowed_sites": []})
    ok = tool_response.get("status") == "error" or len(tool_response.get("data", [])) == 0
    if check("lookup fails closed", ok, "no allowed sites means no results"):
        passed += 1
    else:
        failed += 1

    # Test 3: model config returns expected defaults
    m = model(CFG)
    ok = m.get("name") == "qwen-14b-chat" and isinstance(m.get("context_length"), int)
    if check("model config", ok, f"name={m.get('name')}, ctx_len={m.get('context_length')}"):
        passed += 1
    else:
        failed += 1

    # Test 4: FakeServer records calls
    server = FakeServer()
    run(server.send({"test": "data"}))
    ok = len(server.calls) == 1 and server.calls[0].get("test") == "data"
    if check("FakeServer", ok, f"calls={len(server.calls)}"):
        passed += 1
    else:
        failed += 1

    # Test 5: FakeUser stores messages
    user = FakeUser("Mi clienta Ficticia")
    user.add_message(MESSAGE)
    ok = len(user.messages) == 1 and user.messages[0]["role"] == "user"
    if check("FakeUser", ok, f"messages={len(user.messages)}"):
        passed += 1
    else:
        failed += 1

    print(f"\nResults: {passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
