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

print("The Protected Chat filter")
f = filter_mod.Filter()
body = {"features": {"web_search": True, "image_generation": True, "code_interpreter": True, "memory": True, "voice": True},
        "metadata": {"tool_servers": [{"url": "x"}]}}
md = {"tool_servers": [{"url": "x"}], "features": {"web_search": True}}
out = run(f.inlet(body, __metadata__=md))
check("only the lookup tool is left", out["tool_ids"] == ["protected_lookup"], out["tool_ids"])
check("web search, images, code interpreter and memory are off",
      not any(out["features"][k] for k in ("web_search", "image_generation", "code_interpreter", "memory")), out["features"])
check("other features stay (voice)", out["features"]["voice"] is True)
check("terminals and the browser's tool servers are dropped",
      out["terminal_id"] is None and md["tool_servers"] is None and out["metadata"]["tool_servers"] is None
      and md["features"]["web_search"] is False)
check("it can't be switched off in a chat (no toggle)", not getattr(f, "toggle", False))
check("it runs after the other filters", f.valves.priority > 0)

print("Personal data is removed before anything is sent")
chat_text = MESSAGE + "\n" + DOCUMENT
cases = [
    ("terminación anticipada acuerdo mantenimiento Ficticia Inventado", ["Ficticia", "Inventado"]),
    ("Fulana Ficticia documento 1.111.111.111 acuerdo mantenimiento equipo", ["Fulana", "Ficticia", "1.111.111.111"]),
    ("expediente 99999-00-00-000 estado del trámite", ["99999-00-00-000"]),
    ("acuerdo mantenimiento persona@ejemplo.invalid 300 000 0000 notificación", ["persona@ejemplo.invalid", "300 000 0000"]),
    ("mantenimiento equipo Calle 00 # 00-00 normas", ["Calle 00 # 00-00"]),
    ("cuota mensual 2350 incremento permitido mantenimiento", ["2350"]),
    ("reclamo del señor Perengano Prueba mantenimiento", ["Perengano", "Prueba"]),
    ("mantenimiento 12345-67-89-012 terminación anticipada", ["12345-67-89-012"]),
]
for query, gone in cases:
    clean, removed, refusal = lookup_mod.check_query(query, CFG, chat_text)
    left = [g for g in gone if g in clean]
    check(f"removed from {query!r}", not left and not refusal, f"left {left} in {clean!r}; refusal {refusal!r}")
kept = [
    "Reglamento Técnico norma 1973 terminación anticipada mantenimiento",
    "sección 123 de 2003 mantenimiento equipos industriales",
    "código R-123 de 2001 requisitos de seguridad",
    "Reglamento Técnico norma 384 instalación equipo",
    "Boletín Oficial mantenimiento",
]
for query in kept:
    clean, removed, refusal = lookup_mod.check_query(query, CFG, chat_text)
    check(f"kept as it is: {query!r}", clean == query and not removed and not refusal, f"{clean!r} {removed} {refusal!r}")
clean, removed, refusal = lookup_mod.check_query("Ficticia Inventado 1.111.111.111", CFG, chat_text)
check("refused when nothing general is left", bool(refusal), clean)
clean, removed, refusal = lookup_mod.check_query("mantenimiento site:other.example normas", CFG, "")
check("a site: filter written by the model is dropped (the tool sets it)", "other.example" not in clean, clean)

print("The confirmation comes first, and the user decides")
tool = lookup_mod.Tools()
server = FakeServer([{"url": "https://www.rules.example/norma.html", "title": "Norma", "content": "texto"}])
server.install(tool)
out, user = lookup(tool, "terminación acuerdo mantenimiento Ficticia", user=FakeUser(False))
check("declined: nothing sent, the model is told", not server.requests and "did not approve" in out and len(user.asked) == 1, out)
asked = user.asked[0]["data"]["message"] if user.asked else ""
check("the box shows the exact query with the site filter, and what was removed",
      "Query: terminación acuerdo mantenimiento site:rules.example" in asked and "Ficticia" in asked.split("Removed")[-1], asked)
check("the query in the box carries no personal data", "Ficticia" not in asked.split("Removed")[0], asked)
