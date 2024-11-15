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
body = {"tool_ids": ["search_gateway", "server:mcp:x"], "terminal_id": "t1",
        "features": {"web_search": True, "image_generation": True, "code_interpreter": True, "memory": True, "voice": True},
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

print("The lookup fails closed")
for label, cfg in (("no chat type settings", {}), ("an empty site list", {"allowed_sites": []}),
                   ("only invalid sites", {"allowed_sites": ["https://", "not a domain"]})):
    tool = lookup_mod.Tools()
    server = FakeServer([])
    server.install(tool)
    out, user = lookup(tool, "Reglamento Técnico norma 1973", cfg=cfg)
    check(f"{label}: refused, nothing asked or sent", "switched off" in out and not user.asked and not server.requests, out)
tool = lookup_mod.Tools()
server = FakeServer([])
server.install(tool)
out = run(tool.protected_lookup("Reglamento Técnico norma 1973", __metadata__={"model_id": "chat-test"},
                                __model__=model(CFG), __messages__=[], __files__=[], __event_call__=None))
check("no confirmation box possible: refused, nothing sent", "refused" in out and not server.requests, out)
out = run(tool.protected_lookup("Reglamento Técnico norma 1973", __metadata__={"model_id": "chat-other"},
                                __model__=model(CFG), __messages__=[], __files__=[], __event_call__=FakeUser(True).call))
check("the task model's settings aren't taken for another preset", "switched off" in out, out)

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

tool = lookup_mod.Tools()
order = []
server = FakeServer([
    {"url": "https://evil.example/rules.example/x", "title": "Elsewhere", "content": "x"},
    {"url": "https://notrules.example/a", "title": "Lookalike", "content": "x"},
    {"url": "https://www.rules.example/norma.html", "title": "Norma oficial", "content": "texto"},
    {"url": "https://rules.example.evil.example/b", "title": "Lookalike 2", "content": "x"},
])
server.install(tool)
orig_get = tool._get
tool._get = lambda path, params: (order.append(path), orig_get(path, params))[1]
user = FakeUser(True)
user.call_orig = user.call


async def call(event):
    order.append("confirm")
    return await user.call_orig(event)


user.call = call
out, _ = lookup(tool, "terminación acuerdo mantenimiento", user=user)
check("approved: confirmation, then search, then reading", order == ["confirm", "/search", "/read"], order)
check("the search carries the site filter", server.requests and server.requests[0][1]["q"].endswith("site:rules.example"),
      server.requests[:1])
check("results outside the allowed sites are dropped", "evil.example" not in out and "notrules" not in out
      and "Lookalike" not in out and "Norma oficial" in out, out[:400])
check("the best allowed page is the one read", server.requests[-1] == ("/read", {"url": "https://www.rules.example/norma.html"}),
      server.requests[-1:])
check("the page's text reaches the model, with its link", "Texto oficial" in out and "https://www.rules.example/norma.html" in out)
tool = lookup_mod.Tools()
server = FakeServer([{"url": "https://other.example/a", "title": "x", "content": "x"}])
server.install(tool)
out, _ = lookup(tool, "terminación acuerdo mantenimiento")
check("only results elsewhere: nothing read, said so", [p for p, _ in server.requests] == ["/search"]
      and "Nothing found" in out and "dropped" in out, out)
check("subdomains count as the site, lookalikes don't",
      lookup_mod.on_allowed_site("https://a.b.rules.example/x", ["rules.example"])
      and not lookup_mod.on_allowed_site("https://rules.example.evil.example/", ["rules.example"])
      and not lookup_mod.on_allowed_site("ftp://rules.example/", ["rules.example"]))

print("Chat type files (install/chat-types.sh)")
example, problems = chat_types.load_chat_type(ROOT / "open-webui" / "chat-types" / "example.json")
check("the example chat type is valid", not problems, problems)
preset = chat_types.build_preset(example, "local", [], {"id": "k1", "name": "K", "description": "d"})
meta = preset["meta"]
check("its preset: base model, only the filter and the lookup", preset["base_model_id"] == "local"
      and meta["filterIds"] == ["protected_chat"] and meta["toolIds"] == ["protected_lookup"], json.dumps(meta)[:300])
check("its preset: web search, images, code, terminal, memory off; files, vision, citations on",
      not any(meta["capabilities"][k] for k in ("web_search", "image_generation", "code_interpreter", "terminal", "memory"))
      and all(meta["capabilities"][k] for k in ("file_upload", "vision", "citations")))
check("its preset: built-in tools that reach out are off",
      not any(meta["builtinTools"][k] for k in ("web_search", "code_interpreter", "memory", "chats", "notes",
                                                  "automations", "notifications", "subagents", "tools", "skills")))
check("its preset: allowed sites and patterns in the metadata, Knowledge attached",
      meta["protected_chat"]["allowed_sites"] == example["allowed_sites"] and meta["knowledge"][0]["type"] == "collection")
stored = json.loads(json.dumps(preset))  # what Open WebUI gives back
check("built twice: no differences (a second run changes nothing)", chat_types.differences(preset, stored) == [],
      chat_types.differences(preset, stored))
stored["params"]["system"] = "edited by hand"
stored["meta"]["toolIds"] = ["search_gateway"]
check("a hand edit is seen and undone", set(chat_types.differences(preset, stored)) == {"system prompt", "toolIds"},
      chat_types.differences(preset, stored))
check("the lookup reads the preset it builds", lookup_mod.clean_sites(lookup_mod.chat_type_of(meta).get("allowed_sites"))
      == example["allowed_sites"])
with tempfile.TemporaryDirectory() as tmp:
    bad = Path(tmp) / "Bad_Name.json"
    bad.write_text(json.dumps({"name": "x", "system_prompt": "y", "allowed_sites": ["https://a.example/x"],
                               "patterns": [{"regex": "("}], "sources": [{"name": "s", "urls": "not a list"}]}))
    _, problems = chat_types.load_chat_type(bad)
    check("a broken file is refused, with each problem named", len(problems) == 4, problems)
check("source pages become plain text", chat_types.html_to_text(
    "<html><script>x()</script><body><p>Norma 1.&nbsp;Texto</p><nav>menu</nav><p>Dos</p></body></html>")
      == "Norma 1. Texto\n\nDos", repr(chat_types.html_to_text("<p>Norma 1.&nbsp;Texto</p><nav>menu</nav><p>Dos</p>")))

print("The chat types page (services/chat_types_web.py), with a stand-in builder and a temporary folder")
with tempfile.TemporaryDirectory() as tmp:
    import os
    import time as _time

    os.environ["CHAT_TYPES_DIR"] = tmp
    web = load("chat_types_web", ROOT / "services" / "chat_types_web.py")
    web.BUILDER = ["echo", "builder"]

    def wait_job():
        for _ in range(100):
            if not web.job()["running"]:
                return web.job()
            _time.sleep(0.05)
        return web.job()

    good = {"name": "Page test", "system_prompt": "Be brief.", "allowed_sites": ["https://Example.org/path"],
            "patterns": [{"name": "member", "regex": r"M-\d+"}], "users": ["", "a@b.example"]}
    status, out = web.save({"id": "page-test", "new": True, "data": good})
    job = wait_job()
    path = Path(tmp) / "page-test.json"
    saved = json.loads(path.read_text()) if path.exists() else {}
    check("a new chat type is written and built (only that one)", status == 200 and job["exit"] == 0
          and "builder --only page-test" in job["output"], (status, out, job))
    check("its file: mode 600, the site cleaned to a domain, empty entries dropped",
          oct(path.stat().st_mode & 0o777) == "0o600" and saved.get("allowed_sites") == ["example.org"]
          and saved.get("users") == ["a@b.example"], saved)
    status, out = web.save({"id": "page-test", "new": True, "data": good})
    check("a second chat type with the same id is refused", status == 400, out)
    for label, body in (("an id with capitals or spaces", {"id": "Bad Id", "new": True, "data": good}),
                        ("a broken regular expression", {"id": "page-test", "data": {**good, "patterns": [{"name": "x", "regex": "("}]}}),
                        ("no allowed websites", {"id": "page-test", "data": {**good, "allowed_sites": []}})):
        before = path.read_text()
        status, out = web.save(body)
        check(f"refused, file unchanged: {label}", status == 400 and out.get("problems") and path.read_text() == before, out)
    path.write_text(json.dumps({"_note": "kept", **saved}))
    status, _ = web.save({"id": "page-test", "data": {**good, "name": "Renamed"}})
    wait_job()
    saved = json.loads(path.read_text())
    check("an edit keeps notes written by hand", status == 200 and saved.get("_note") == "kept" and saved["name"] == "Renamed", saved)
    status, _ = web.delete({"id": "page-test"})
    job = wait_job()
    check("delete: removed from Open WebUI by the builder, file moved to .deleted/",
          status == 200 and "builder --remove page-test" in job["output"] and not path.exists()
          and len(list((Path(tmp) / ".deleted").glob("page-test-*.json"))) == 1, job)

    class FakeHandler:
        def __init__(self, client="127.0.0.1", host="127.0.0.1:8090", ctype="application/json", origin=None):
            self.client_address = (client, 50000)
            self.headers = {"Host": host, "Content-Type": ctype, **({"Origin": origin} if origin else {})}
            self.sent = None

        def _send(self, status, data):
            self.sent = (status, data)

        def _send_bytes(self, status, payload, ctype):
            self.sent = (status, ctype)

    def ask(method, path, **kw):
        h = FakeHandler(**kw)
        web.handle(h, path, method, b'{"id": "x"}', "/nonexistent.db", 8090)
        return h.sent[0]

    check("the page is served to this computer", ask("GET", "/chat-types") == 200)
    check("its CSS and JS are served to this computer",
          ask("GET", "/chat-types/chat_types_page.css") == 200 and ask("GET", "/chat-types/chat_types_page.js") == 200)
    check("refused: another machine (even with SEARCH_GATEWAY_BIND)", ask("GET", "/chat-types/data", client="192.168.1.20") == 403)
    check("refused: a foreign Host name (DNS rebinding)", ask("GET", "/chat-types/data", host="evil.example:8090") == 403)
    check("refused: a plain form post (other websites can send those)", ask("POST", "/chat-types/delete", ctype="application/x-www-form-urlencoded") == 403)
    check("refused: a post from another website's page", ask("POST", "/chat-types/delete", origin="http://evil.example") == 403)
    check("other paths are left to the server", web.handle(FakeHandler(), "/search", "GET", b"", "", 8090) is False)

print(f"protected-chat-check.py: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
