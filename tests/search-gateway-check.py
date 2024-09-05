#!/usr/bin/env python3
"""search-gateway-check.py: the search gateway on its own, with stand-in search services and temporary files.

    ~/.local/share/localllm/search-gateway-venv/bin/python -I tests/search-gateway-check.py

Run by tests/guard-tests.mjs with the gateway's own Python (install/search-gateway.sh makes it: the settings page
needs the real pydantic). No network, no model; nothing live is touched (temporary settings file, state database
and Open WebUI database). Checks:
  - the state database: usage updates from many threads at once all count; cache; the search log;
  - searches at the same time run side by side (four 1-second searches take about 1 second, not 4);
  - searches still running count against an allowance, so they don't all pass its last bit;
  - the settings page: only this computer and its own page may change settings; keys are never sent back; a key left
    empty is kept; wrong values are refused; the file is only yours and holds only what differs from the defaults;
  - Open WebUI's thin client: passes calls on, shows the gateway's status lines and citations, says so when it's down;
  - the migration from Open WebUI: settings out of its database, usage, cache and log into the gateway's;
  - nothing in the gateway reads Open WebUI or its database (only the chat types page, for the protected chats).
"""

import asyncio
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
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


try:
    import aiohttp  # noqa: F401
    import pydantic  # noqa: F401
except ImportError:
    print("search-gateway-check.py: needs the gateway's Python (run install/search-gateway.sh); 0 passed, 1 failed")
    sys.exit(1)

tmp = Path(tempfile.mkdtemp(prefix="search-gateway-check-"))
os.environ["SEARCH_GATEWAY_DB"] = str(tmp / "state.db")
os.environ["SEARCH_GATEWAY_SETTINGS"] = str(tmp / "search-gateway.json")
os.environ["WEBUI_DB"] = str(tmp / "no-webui.db")
eng = load("search_gateway_engine", ROOT / "services" / "search_gateway_engine.py")
srv = load("search_gateway", ROOT / "services" / "search_gateway.py")
threading.Thread(target=srv.LOOP.run_forever, daemon=True).start()

# ---------------------------------------------------------------- the state database
print("The state database")


def bump(data):
    data["n"] = data.get("n", 0) + 1
    return data


threads = [threading.Thread(target=lambda: [eng._doc_update("test", bump) for _ in range(50)]) for _ in range(8)]
[t.start() for t in threads]
[t.join() for t in threads]
con = eng._db()
n = json.loads(con.execute("SELECT data FROM doc WHERE name = 'test'").fetchone()[0])["n"]
mode = con.execute("PRAGMA journal_mode").fetchone()[0]
con.close()
check("8 threads x 50 updates at once: all 400 counted", n == 400, n)
check("WAL mode: reading never waits for a search's write", mode == "wal", mode)
check("only you can read it (0600)", oct(os.stat(tmp / "state.db").st_mode & 0o777) == "0o600")

# ---------------------------------------------------------------- searches side by side, through the real server
print("Searches at the same time")
# Settings: no services but a stand-in SearXNG, which takes 1 second per search.
srv.write_settings({"you_mcp_url": "", "searxng_url": "http://stand-in.invalid/search", "cache_hours": 0})
tool = srv._tool()
type(tool)._searxng = lambda self, session, query, time_range: _slow(query)


async def _slow(query):
    await asyncio.sleep(1.0)
    return [{"url": f"https://example.com/{query.replace(' ', '-')}", "title": query, "snippet": "s"}]


server = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
server.daemon_threads = True
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_address[1]}"


def get(path, headers=None):
    req = urllib.request.Request(base + path, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def post(path, data, headers=None, raw=None, ctype="application/json"):
    body = raw if raw is not None else json.dumps(data).encode()
    req = urllib.request.Request(base + path, data=body, method="POST", headers={"Content-Type": ctype, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


results = {}
started = time.time()
apps = ["Pi", "Open WebUI", "Pi", "Protected Lookup"]
workers = [threading.Thread(target=lambda i=i: results.__setitem__(i, get(f"/search?q=query+{i}&app={apps[i].replace(' ', '+')}")))
           for i in range(4)]
[w.start() for w in workers]
[w.join() for w in workers]
took = time.time() - started
check("four 1-second searches at once take about 1 second (not 4)", took < 2.5, f"{took:.1f} s")
check("each got its own results", all(results[i][1]["results"][0]["url"].endswith(f"query-{i}") for i in range(4)),
      [results[i][1].get("results") for i in range(4)])
apps_logged = sorted(e.get("app") for e in eng.read_log(started - 1))
check("the log names each app", apps_logged == sorted(apps), apps_logged)

# A tool call from Open WebUI and a Pi search at the same time.
both = {}
started = time.time()
w1 = threading.Thread(target=lambda: both.__setitem__("owui", post("/tool/web_search", {
    "args": {"query": "owui words"}, "metadata": {"message_id": "m1", "chat_id": "c1"}, "user_text": "owui words?"})))
w2 = threading.Thread(target=lambda: both.__setitem__("pi", get("/search?q=pi+words")))
[w.start() for w in (w1, w2)]
[w.join() for w in (w1, w2)]
check("Open WebUI's tool call and Pi's search run side by side", time.time() - started < 2.0, f"{time.time() - started:.1f} s")
status, data = both["owui"]
check("tool call: the result text and the status events", status == 200 and "owui-words" in data["result"]
      and any(e["type"] == "status" for e in data["events"]), data)
check("tool call: an unknown function is refused", post("/tool/rm_rf", {})[0] == 404)

# ---------------------------------------------------------------- in-flight searches and allowances
print("Searches still running count against an allowance")
t2 = srv._tool()
t2._in_flight.clear()
u = {"tavily": 10, "serpapi": 100.0, "you": {"day": eng._day(), "count": 3}}
t2._in_flight.update({"tavily": 2, "serpapi": 3, "you": 1})
w = t2._with_in_flight(u)
check("Tavily: 2 running count as 2 more", w["tavily"] == 12, w)
check("SerpApi: 3 running count as 3 searches' cost", w["serpapi"] == 100.0 + 3 * t2._simple_cost("serpapi", u), w)
check("You.com: today's count includes the running one", w["you"]["count"] == 4, w)
check("the stored usage itself is untouched", u["tavily"] == 10 and u["you"]["count"] == 3)
t2._in_flight.clear()
check("nothing running: the same usage", t2._with_in_flight(u) is u)

# ---------------------------------------------------------------- settings page
print("Settings page")
LOCAL = {"Host": f"127.0.0.1:{server.server_address[1]}"}
for asset, ctype in (("/settings", "text/html"), ("/settings/search_gateway_settings.css", "text/css"),
                     ("/settings/search_gateway_settings.js", "application/javascript")):
    with urllib.request.urlopen(urllib.request.Request(base + asset, headers=LOCAL), timeout=10) as r:
        check(f"GET {asset} served locally", r.status == 200 and r.headers["Content-Type"].startswith(ctype))
status, data = get("/settings/data", LOCAL)
fields = {f["name"]: f for f in data.get("fields", [])}
check("the page lists every setting", status == 200 and "service_order" in fields and len(fields) == len(tool.Valves.model_fields))
check("API keys: only set / not set, never their value", all("value" not in f for f in fields.values() if f["secret"])
      and fields["tavily_api_key"]["secret"])
status, data = post("/settings/save", {"values": {"tavily_api_key": "tvly-SECRET-1", "max_searches": 5}},
                    {**LOCAL, "Origin": f"http://127.0.0.1:{server.server_address[1]}"})
saved = json.loads((tmp / "search-gateway.json").read_text())
check("save: written, the changed names reported", status == 200 and data["changed"] == ["max_searches", "tavily_api_key"], data)
check("the file holds only what differs from the defaults", saved == {"you_mcp_url": "", "searxng_url": "http://stand-in.invalid/search",
                                                                     "cache_hours": 0, "tavily_api_key": "tvly-SECRET-1", "max_searches": 5}, saved)
check("the file is only yours (0600)", oct(os.stat(tmp / "search-gateway.json").st_mode & 0o777) == "0o600")
check("the next search uses it", srv._tool().valves.max_searches == 5 and srv._tool().valves.tavily_api_key == "tvly-SECRET-1")
status, data = get("/settings/data", LOCAL)
check("a saved key isn't sent back either", "tvly-SECRET-1" not in json.dumps(data) and
      {f["name"]: f for f in data["fields"]}["tavily_api_key"]["set"])
post("/settings/save", {"values": {"tavily_api_key": ""}}, LOCAL)
check("a key left empty is kept", json.loads((tmp / "search-gateway.json").read_text()).get("tavily_api_key") == "tvly-SECRET-1")
post("/settings/save", {"clear": ["tavily_api_key"]}, LOCAL)
check("remove: the key is gone", "tavily_api_key" not in json.loads((tmp / "search-gateway.json").read_text()))
status, data = post("/settings/save", {"values": {"max_searches": "lots"}}, LOCAL)
check("a wrong value is refused, the file unchanged", status == 400 and "max_searches" in data["problems"][0]
      and json.loads((tmp / "search-gateway.json").read_text())["max_searches"] == 5, data)
check("an unknown setting is refused", post("/settings/save", {"values": {"rm": 1}}, LOCAL)[0] == 400)
before = (tmp / "search-gateway.json").read_text()
check("refused: a post from another website (Origin)", post("/settings/save", {"values": {"max_searches": 1}},
      {**LOCAL, "Origin": "http://evil.example"})[0] == 403)
check("refused: a plain form post (what another site could send)", post("/settings/save", None, LOCAL,
      raw=b"values=1", ctype="application/x-www-form-urlencoded")[0] == 403)
check("refused: another host name pointed at this computer (DNS rebinding)",
      get("/settings/data", {"Host": f"evil.example:{server.server_address[1]}"})[0] == 403)
check("nothing changed by the refused requests", (tmp / "search-gateway.json").read_text() == before)
check("the settings log names, never values", "tvly-SECRET-1" not in json.dumps(srv.settings_data(srv._tool())))

# ---------------------------------------------------------------- Open WebUI's thin client
print("Open WebUI's thin client (open-webui/tools/search_gateway.py)")
thin = load("thin_search_gateway", ROOT / "open-webui" / "tools" / "search_gateway.py").Tools()
thin.valves.gateway_url = base
events = []


async def emit(event):
    events.append(event)


out = asyncio.run(thin.web_search("thin words", __messages__=[{"role": "user", "content": "thin words?"}],
                                  __metadata__={"message_id": "m9", "chat_id": "c9"}, __event_emitter__=emit))
check("web_search: the gateway's result text", "thin-words" in out, out[:200])
check("web_search: its status lines shown in the chat", [e for e in events if e["type"] == "status"], events)
engine_doc = {n: getattr(eng.Tools, n).__doc__ for n in ("web_search", "read_page", "plan_research")}
thin_doc = {n: getattr(type(thin), n).__doc__ for n in engine_doc}
check("the model sees the same tool: same functions and descriptions as the engine's", engine_doc == thin_doc)
thin.valves.gateway_url = "http://127.0.0.1:9"  # nothing listens there
down = asyncio.run(thin.web_search("x", __event_emitter__=emit))
check("gateway down: says search is unavailable and how to start it", "isn't answering" in down and "./start.sh" in down, down)
thin_src = (ROOT / "open-webui" / "tools" / "search_gateway.py").read_text()
check("the thin client keeps no keys, counts or cache", "api_key" not in thin_src and "sqlite" not in thin_src
      and set(type(thin).Valves.model_fields) == {"gateway_url", "timeout_seconds"})

# ---------------------------------------------------------------- migration from Open WebUI
print("Migration from Open WebUI (services/search_gateway_migrate.py)")
mig_dir = tmp / "old-owui"
mig_dir.mkdir()
webui = sqlite3.connect(mig_dir / "webui.db")
webui.execute("CREATE TABLE tool (id TEXT PRIMARY KEY, valves TEXT)")
webui.execute("INSERT INTO tool VALUES ('local_web_research', ?)", (json.dumps({
    "service_order": "tavily, exa", "tavily_api_key": "tvly-OLD", "max_searches": 8,  # 8 is the default
    "usage_file": "~/.open-webui/web_research_usage.json", "gone_setting": 1}),))
webui.commit()
webui.close()
(mig_dir / "web_research_usage.json").write_text(json.dumps({"month": eng._month(), "tavily": 42}))
(mig_dir / "web_research_usage.json.lock").write_text("")
(mig_dir / "search_gateway_cache.json").write_text(json.dumps({"k|": {"t": time.time(), "provider": "tavily", "results": [{"url": "u"}]}}))
(mig_dir / "web_research_log.jsonl").write_text(json.dumps({"t": time.time() - 60, "app": "Pi", "service": "tavily"}) + "\n")
mig_env = {**os.environ, "SEARCH_GATEWAY_SETTINGS": str(mig_dir / "gw.json"), "SEARCH_GATEWAY_DB": str(mig_dir / "gw.db")}
import subprocess  # noqa: E402
run = lambda: subprocess.run([sys.executable, "-I", str(ROOT / "services" / "search_gateway_migrate.py"), str(mig_dir / "webui.db")],
                             env=mig_env, capture_output=True, text=True)
r = run()
settings = json.loads((mig_dir / "gw.json").read_text()) if (mig_dir / "gw.json").exists() else {}
check("settings: moved out of Open WebUI, only what differs from the defaults", settings == {"service_order": "tavily, exa",
      "tavily_api_key": "tvly-OLD"}, settings)
check("settings file only yours (0600)", (mig_dir / "gw.json").exists() and oct(os.stat(mig_dir / "gw.json").st_mode & 0o777) == "0o600")
check("never prints a value", "tvly-OLD" not in r.stdout + r.stderr, r.stdout + r.stderr)
g = sqlite3.connect(mig_dir / "gw.db")
usage = json.loads(g.execute("SELECT data FROM doc WHERE name = 'usage'").fetchone()[0])
check("state: usage counts, cache and log in the gateway's database", usage.get("tavily") == 42
      and g.execute("SELECT count(*) FROM cache").fetchone()[0] == 1 and g.execute("SELECT count(*) FROM log").fetchone()[0] == 1)
g.close()
check("the old files next to Open WebUI's database are gone", not any(p.name != "webui.db" and not p.name.startswith("gw.")
      for p in mig_dir.iterdir()), sorted(p.name for p in mig_dir.iterdir()))
r2 = run()
check("run again: nothing to do", r2.returncode == 0 and "moved" not in r2.stdout, r2.stdout)

# ---------------------------------------------------------------- long pages: parts and #sections
print("Long pages: parts and #sections (read_page)")
# A real headless browser read of a local page: a table of contents, then sections with ids, longer than one part.
filler = "Some prose about this feature that goes on for a while. " * 40
html_page = ["<html><body><nav><ul>"] + [f'<li><a href="#s{i}">Topic {i}</a></li>' for i in range(12)] + ["</ul></nav>"]
html_page += [f'<section id="s{i}"><h2>Topic {i}</h2><p>Section {i} text. {filler}</p></section>' for i in range(12)]
html_page += ['<h3 id="uuid">uuid</h3><p>UUID versions 6, 7 and 8 are new. ' + filler + "</p></body></html>"]
page_file = tmp / "long.html"
page_file.write_text("\n".join(html_page))
real = eng.Tools()
text, problem, anchors = asyncio.run(real._read_local(page_file.as_uri()))
check("the local browser reads the page and finds its sections", not problem and {"s0", "s5", "uuid"} <= set(anchors),
      f"{problem} {sorted(anchors)[:20]}")
check("a section starts where its heading is", text[anchors["uuid"]:].startswith("uuid")
      and text[anchors["s5"]:].startswith("Topic 5"), repr(text[anchors.get("uuid", 0):][:40]))
check("no section marks left in the text", eng.SECTION_MARK not in text and eng.SECTION_END not in text)

# The page reader, with the browser replaced by the page above (read once, then kept).
LONG_URL = "https://docs.example/long.html"
reads = []


async def _fake_local(self, url):
    reads.append(url)
    return text, "", anchors


type(srv._tool())._read_local = _fake_local
srv.write_settings({"you_mcp_url": "", "searxng_url": "", "max_page_chars": 10000, "max_page_reads": 20})
t = srv._tool()
meta = {"message_id": "long-1"}
p1 = asyncio.run(t.read_page(LONG_URL, __metadata__=meta))
total = len(eng._parts(text, 10000))
check(f"a long page comes in parts ({total}), the first says how to read on",
      total > 2 and f"(part 1 of {total})" in p1.split("\n", 1)[0] and "part=2" in p1, p1[-400:])
check("the note lists the later sections to jump to", "#uuid" in p1 and "#s11" in p1, p1[-400:])
p2 = asyncio.run(t.read_page(LONG_URL, 2, __metadata__=meta))
check("part 2 continues where part 1 ended", f"(part 2 of {total})" in p2.split("\n", 1)[0]
      and p2.split("\n\n", 1)[1][:200] in text and p2.split("\n\n", 1)[1][:50] not in p1)
check("the next part is kept from the first read (no new fetch)", len(reads) == 1, reads)
sec = asyncio.run(t.read_page(LONG_URL + "#uuid", __metadata__=meta))
check("#section: the text starts at that heading", sec.split("\n\n", 1)[1].startswith("uuid\n")
      and "UUID versions 6, 7 and 8" in sec, sec[:200])
check("#section in the middle: its parts are counted from there", "from section #s5" in
      asyncio.run(t.read_page(LONG_URL + "#s5", __metadata__=meta)))
miss = asyncio.run(t.read_page(LONG_URL + "#nothing-here", __metadata__=meta))
check("an unknown #section says so and gives the start", "wasn't found" in miss and "Topic 0" in miss[:400], miss[-300:])
check("a part past the end says how many there are", "doesn't exist" in asyncio.run(t.read_page(LONG_URL, 99, __metadata__=meta)))
# Pages from a fallback service have no section marks: the heading is found by its words, past the contents list.
plain = "Contents\nIntro\nInstall\nUsage\n\nIntro\n" + filler + "\n\nInstall\n" + "Run the installer and wait. " * 30
check("no marks: a #section's heading is found by its words, not in the contents list",
      plain[eng._heading_offset(plain, "install"):].startswith("Install\nRun"), eng._heading_offset(plain, "install"))
# Open WebUI's thin client passes part on; the protected lookup's /read gets the text without the note.
status, data = post("/tool/read_page", {"args": {"url": LONG_URL, "part": 2}, "metadata": {"message_id": "long-2"}})
check("Open WebUI's tool call passes part on", status == 200 and f"(part 2 of {total})" in data["result"], str(data)[:300])
status, data = get("/read?url=" + urllib.request.quote(LONG_URL, safe=""))
check("/read (protected lookup): the text without the note on parts", status == 200 and "part=2" not in data.get("text", "x")
      and data.get("text", "").startswith("Topic 0"), str(data)[-300:])
srv.write_settings({"you_mcp_url": "", "searxng_url": "http://stand-in.invalid/search", "cache_hours": 0})

# ---------------------------------------------------------------- the services' own usage reports
print("Usage reported by the services")
check("Bright Data: today's reply format (zone -> day -> domain -> requests) is counted",
      eng._sum_req({"serp_api1": {"2026-11-12T00:00:00.000Z": {"google.com": 11}, "2026-11-13T00:00:00.000Z": {"google.com": 4}}}) == 15)
check("Bright Data: the older format (req fields) still counts", eng._sum_req({"a": [{"req": 3}, {"req": 4, "bw": 999}]}) == 7)
check("Bright Data: a reply without counts is reported, not taken as 0", eng._sum_req({"error": "unauthorized"}) is None)
check("Parallel's balance isn't asked for (its Account API takes no API key)", not hasattr(eng.Tools, "_check_parallel"))

# ---------------------------------------------------------------- not tied to Open WebUI
print("Not tied to Open WebUI")
for name in ("search_gateway_engine.py", "search_gateway.py"):
    src = (ROOT / "services" / name).read_text()
    code = "\n".join(l for l in src.split("\n") if not l.lstrip().startswith("#"))
    uses = [l.strip() for l in code.split("\n") if ("webui.db" in l or ".open-webui" in l or "open_webui" in l) and "chat-types" not in l
            and "chat_types" not in l and "/chat-types" not in l and "WEBUI_DB" not in l]
    if name == "search_gateway.py":
        uses = [u for u in uses if not u.startswith(("WEBUI_DB", "DB_PATH")) and "only for /chat-types" not in u]
    check(f"services/{name}: doesn't read Open WebUI or its database", not uses, uses)
check("this check ran without any Open WebUI database", not (tmp / "no-webui.db").exists())

server.shutdown()
print(f"search-gateway-check.py: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
