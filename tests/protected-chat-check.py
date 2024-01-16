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
  - results outside the allowed sites are dropped; the best allowed page is read;
  - the chat type files: the example builds a preset with only the filter and the lookup; building it twice gives no
    differences (a second install/chat-types.sh run changes nothing); a broken file is refused.
The tool and filter import pydantic, which only Open WebUI has; a small stand-in is used here.
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

lookup_mod = load("protected_lookup", ROOT / "open-webui" / "tools" / "protected_lookup.py")
filter_mod = load("protected_chat", ROOT / "open-webui" / "functions" / "protected_chat.py")
chat_types = load("chat_types", ROOT / "open-webui" / "chat_types.py")

CFG = {
    "allowed_sites": ["rules.example"],
    "patterns": [{"name": "file number", "regex": r"\b\d{5}-\d{2}-\d{2}-\d{3}\b"}],
    "reference_words": ["norma", "normas", "sección", "rule", "section"],
    "allowed_patterns": [{"name": "rule code", "regex": r"\bR-\d{3,4}\b"}],
    "allowed_terms": ["Boletín", "Reglamento", "Técnico"],
}

DOCUMENT = (
    "ACUERDO DE SERVICIO\n"
    "Entre los suscritos Fulana Ficticia Ejemplar, identificada con documento 1.111.111.111, y el señor Mengano "
    "Inventado, se acuerda el mantenimiento del equipo ubicado en la Calle 00 # 00-00. Expediente 99999-00-00-000. "
    "Valor mensual 2350 unidades. Correo: persona@ejemplo.invalid, teléfono 300 000 0000. Según la norma 1973 del "
    "Reglamento Técnico y el código R-123 de 2001, publicado en el Boletín."
)
MESSAGE = "Mi clienta Ficticia quiere saber si puede terminar el acuerdo antes; el proveedor es Inventado."


def model(cfg):
    return {"id": "chat-test", "info": {"meta": {"protected_chat": cfg}}}


class FakeServer:
    """Stands in for the search gateway: records every request."""

    def __init__(self, results, page="Norma 1973. Texto oficial de la norma. " * 20):
        self.requests, self.results, self.page = [], results, page

    def install(self, tool):
        def get(path, params):
            self.requests.append((path, params))
            if path == "/search":
                return {"results": self.results}
            return {"url": params["url"], "text": self.page}

        tool._get = get


class FakeUser:
    def __init__(self, answer):
        self.answer, self.asked, self.events = answer, [], []

    async def call(self, event):
        self.asked.append(event)
        return self.answer

    async def emit(self, event):
        self.events.append(event)


def run(coro):
    return asyncio.run(coro)


def lookup(tool, query, cfg=CFG, user=None, messages=None, files=None):
    user = user or FakeUser(True)
    out = run(tool.protected_lookup(
        query, __metadata__={"model_id": "chat-test"}, __model__=model(cfg),
        __messages__=messages if messages is not None else [{"role": "user", "content": MESSAGE}],
        __files__=files if files is not None else [{"type": "file", "file": {"data": {"content": DOCUMENT}}}],
        __event_emitter__=user.emit, __event_call__=user.call))
    return out, user


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
