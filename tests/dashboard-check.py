#!/usr/bin/env python3
"""dashboard-check.py: the search usage dashboard and the engine's search log, on a sample state database.

    python3 -I tests/dashboard-check.py

Run by tests/guard-tests.mjs; no network, no model, nothing live is touched (a temporary state database holds the
usage counts, cache and log). Checks:
  - /usage.json against sample usage counts and settings: the numbers, the order (temporary order), and the states
    "available", "near reserve", "used up" and "no key";
  - the page and /usage.json load from a real server on a free port;
  - the search log: one entry per search, the app (Pi or Open WebUI), cached searches, no search words unless
    log_search_words is on, and entries older than SEARCH_LOG_DAYS dropped.
The engine imports aiohttp and pydantic (the gateway's own Python has them); small stand-ins are used without them.
"""

import asyncio
import datetime
import importlib.util
import json
import os
import sys
import tempfile
import threading
import types
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL  {name}" + (f"\n        {detail}" if detail else ""))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Session:  # aiohttp.ClientSession stand-in: no request is ever made in these checks
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


if importlib.util.find_spec("aiohttp") is None:
    sys.modules["aiohttp"] = types.SimpleNamespace(ClientSession=_Session, ClientTimeout=lambda **k: None)
if importlib.util.find_spec("pydantic") is None:
    class BaseModel:
        def __init__(self, **values):
            for k, v in values.items():
                setattr(self, k, v)

    sys.modules["pydantic"] = types.SimpleNamespace(BaseModel=BaseModel, Field=lambda default=None, **_: default)

tmp = Path(tempfile.mkdtemp(prefix="dashboard-check-"))
os.environ["SEARCH_GATEWAY_DB"] = str(tmp / "state.db")  # read when the engine is loaded
os.environ["SEARCH_GATEWAY_SETTINGS"] = str(tmp / "settings.json")
tool_mod = load_module("search_gateway_engine", ROOT / "services" / "search_gateway_engine.py")
srv = load_module("search_gateway", ROOT / "services" / "search_gateway.py")
threading.Thread(target=srv.LOOP.run_forever, daemon=True).start()
today = datetime.date.today()
month = tool_mod._month()

def make_tool(**valves):
    tool = tool_mod.Tools()
    base = dict(
        service_order="tavily, serpapi/hasdata, brave", temporary_service_order="hasdata, tavily",
        temporary_order_until=(today + datetime.timedelta(days=30)).isoformat(),
        tavily_api_key="KEY-tavily-1", serpapi_api_key="KEY-serpapi-2", hasdata_api_key="KEY-hasdata-3",
        you_mcp_url="", parallel_api_key="", exa_api_key="", brightdata_api_key="", browserbase_api_key="",
        linkup_api_key="", firecrawl_api_key="", contextdev_api_key="", you_api_key="",
    )
    names = list(getattr(type(tool.valves), "model_fields", None) or
                 [k for k in vars(type(tool.valves)) if not k.startswith("_")])
    tool.valves = tool.Valves(**{**{k: getattr(tool.valves, k) for k in names}, **base, **valves})
    return tool


# ---------------------------------------------------------------- /usage.json numbers

usage = {
    "month": month,
    "tavily": 100,                                   # of 950: available
    "serpapi": 180.0, "serpapi_n": 180, "serpapi_u": 180.0,  # 250 free, 50 kept back: 20 searches left -> near reserve
    "hasdata": 760.0, "hasdata_n": 152, "hasdata_u": 760.0,  # 1000 free, 5 per search, 250 kept back: used up
    "real": {"tavily": {"used": 100, "limit": 1000, "t": 1700000000.0, "own": 100}},
}
tool_mod._doc_update("usage", lambda _: usage)


def set_cache(entries: dict):
    con = tool_mod._db()
    con.execute("DELETE FROM cache")
    for key, e in entries.items():
        con.execute("INSERT INTO cache (key, t, provider, results) VALUES (?, ?, ?, ?)",
                    (key, e["t"], e["provider"], json.dumps(e["results"])))
    con.close()


def log_entries():
    return tool_mod.read_log(0)


set_cache({"a|": {"t": 9e9, "provider": "tavily", "results": []}, "b|": {"t": 1.0, "provider": "tavily", "results": []}})
tool = make_tool()
report = srv.usage_report(tool)
rows = {s["id"]: s for s in report["services"]}

check("order: the temporary order applies", report["order"]["temporary"] and report["order"]["active"] == "hasdata, tavily")
check("order: services sorted by the active order", [s["id"] for s in report["services"]][:2] == ["hasdata", "tavily"],
      [s["id"] for s in report["services"]])
t = rows["tavily"]
check("tavily: used/allowance/left", (t["used"], t["allowance"], t["left"]) == (100, 950, 850), t)
check("tavily: available, confirmed by the service", t["status"] == "available" and t["source"] == "service", t)
check("tavily: calendar-month cycle", t["cycle_start"] == f"{month}-01", t)
s = rows["serpapi"]
check("serpapi: near the reserve (20 searches left)", s["status"] == "near reserve" and s["searches_left"] == 20, s)
hd = rows["hasdata"]
check("hasdata: used up (the reserve is reached)", hd["status"] == "used up" and hd["left"] == 0, hd)
check("brave: no key", rows["brave"]["status"] == "no key", rows["brave"])
check("cache: entries and fresh ones", report["cache"]["entries"] == 2 and report["cache"]["fresh"] == 1, report["cache"])
check("history: 30 days", len(report["history"]["days"]) == 30 and report["history"]["days"][-1]["day"] == today.isoformat())
check("tool version read", report["tool_version"] == tool_mod.__doc__.split("version:")[1].split()[0], report["tool_version"])
check("no API key in /usage.json", "KEY-" not in json.dumps(report))

# ---------------------------------------------------------------- the search log

cache_key = "find me"  # words sorted: "find me"
set_cache({f"{cache_key}|": {"t": 9e9, "provider": "tavily", "results": [{"url": "https://x.example"}]}})
turn = tool._turn({"message_id": "m1"})
asyncio.run(tool._search_chain("me find", "", "", turn))
turn_pi = tool._turn({"message_id": "pi-1"})
turn_pi["app"] = "Pi"


async def _fake(session, query, time_range):
    return [{"url": "https://a.example", "title": "A"}, {"url": "https://b.example", "title": "B"}]


async def _broken(session, query, time_range):
    raise tool_mod.ProviderError(500, "upstream said: my private words")


tool._fake, tool._broken = _fake, _broken
tool._provider_order = lambda turn, skip=(): ["broken", "fake"]
asyncio.run(tool._search_chain("my private words", "", "", turn_pi, skip={"tavily"}, fresh=True))
lines = log_entries()
check("log: one entry per search", len(lines) == 2, lines)
check("log: cached search from Open WebUI", lines[0]["cached"] and lines[0]["app"] == "Open WebUI" and lines[0]["service"] == "tavily", lines[0])
check("log: Pi retry with a failed service", lines[1]["app"] == "Pi" and lines[1]["retry"] and lines[1]["failed"] == ["broken"]
      and lines[1]["service"] == "fake" and lines[1]["results"] == 2, lines[1])
check("log: no search words by default", "private" not in json.dumps(lines) and "find" not in json.dumps(lines))
tool.valves.log_search_words = True
asyncio.run(tool._search_chain("words on", "", "", turn_pi))
check("log: search words when log_search_words is on", log_entries()[-1].get("q") == "words on")
tool.valves.log_search_words = False

report = srv.usage_report(tool)
day = report["history"]["days"][-1]
check("history: today's searches per service and app",
      day["total"] == 3 and day["cached"] == 1 and day["services"].get("fake") == 2 and day["apps"] == {"Open WebUI": 1, "Pi": 2}, day)
check("history: latest first", report["history"]["latest"][0].get("q") == "words on" and len(report["history"]["latest"]) == 3)
check("cache hits from the log", report["cache"]["hits_today"] == 1, report["cache"])

import time  # noqa: E402
con = tool_mod._db()
old_t = time.time() - (tool_mod.SEARCH_LOG_DAYS + 1) * 86400
con.execute("INSERT INTO log (t, entry) VALUES (?, ?)", (old_t, json.dumps({"t": old_t, "app": "Pi", "service": "old"})))
con.close()
asyncio.run(tool._search_chain("one more", "", "", turn_pi))
check(f"log: entries older than {tool_mod.SEARCH_LOG_DAYS} days are dropped", all(e.get("service") != "old" for e in log_entries())
      and len(log_entries()) == 4, [e.get("service") for e in log_entries()])
check("history counts every search of today", srv.usage_report(tool)["history"]["days"][-1]["total"] == 4)
check("the state database is only yours (0600)", oct(os.stat(tmp / "state.db").st_mode & 0o777) == "0o600")
