"""
search_gateway.py: version 3.0.0
Runs from this repo as the user service search-gateway (services/search-gateway.service, installed by
install/search-gateway.sh with its own Python environment, started by start/search-gateway.sh).

The one place every web search goes through: Pi's web_search (pi/extensions/search-gateway.ts), Open WebUI's
"Search Gateway" tool (open-webui/tools/search_gateway.py, a thin client) and the Protected Lookup tool
(open-webui/tools/protected_lookup.py). It runs the engine (services/search_gateway_engine.py) itself and needs
neither Open WebUI nor its database:

  - Settings (API keys, service order, limits): ~/.config/localllm/search-gateway.json (only you can read it), edited
    on /settings. A change is used by the next search.
  - Usage counts, result cache and search log: ~/.local/share/localllm/search-gateway.db (SQLite), so the free-tier
    limits (usage_reserve below each one) cover every app together.
  - Searches run side by side: one event loop runs every request, so one app's search never waits for another's.
  - SearXNG is only the engine's own last fallback. A SearXNG !bang left in a query is removed (Pi turns the ones it
    knows into site: filters).

  GET  /search?q=...[&time_range=][&retry_after=][&app=]   results in SearXNG's JSON format, plus "service" and
                                                           "problems"; retry_after: a repeat that skips the cache,
                                                           those services and their retry group
  GET  /read?url=...                                       a page's text (Protected Lookup)
  POST /tool/<web_search|plan_research|read_page>          Open WebUI's tool: {"args", "metadata", "user_text"}
                                                           -> {"result", "events"} (its status lines, citations)
  GET  /dashboard, /usage.json                             the usage dashboard (services/search_gateway_dashboard.html)
  GET  /settings, /settings/data; POST /settings/save      the settings page (services/search_gateway_settings.html)
  GET  /chat-types...                                      Open WebUI's protected chats' chat types (chat_types_web.py)
  GET  /healthz

Listens on 127.0.0.1 only (SEARCH_GATEWAY_BIND changes that). /settings and /chat-types answer only this computer,
and accept changes only as a JSON POST from their own page (not from another website open in your browser).

Environment (all optional):
  SEARCH_GATEWAY_PORT       port                          (default 8090)
  SEARCH_GATEWAY_BIND       address                       (default 127.0.0.1; only ever a private address)
  SEARCH_GATEWAY_SETTINGS   settings file                 (default ~/.config/localllm/search-gateway.json)
  SEARCH_GATEWAY_DB         state database                (default ~/.local/share/localllm/search-gateway.db)
  WEBUI_DB                  Open WebUI's database, only for /chat-types (default ~/.open-webui/webui.db)
  SEARCH_GATEWAY_DEBUG      1 to log each search          (default 0)
  SEARCH_GATEWAY_SEARXNG    SearXNG's search address, instead of the searxng_url setting (the helper PC of the two-PC
                            setup reaches the main PC's SearXNG by its Tailscale name); ignored when SearXNG is off
"""

import asyncio
import calendar
import concurrent.futures
import datetime
import html
import importlib.util
import json
import os
import re
import sys
import tempfile
import threading
import time
import traceback
import types
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("SEARCH_GATEWAY_PORT", "8090"))
BIND = os.environ.get("SEARCH_GATEWAY_BIND", "").strip() or "127.0.0.1"
DASHBOARD_FILE = os.path.join(HERE, "search_gateway_dashboard.html")
DASHBOARD_CSS = os.path.join(HERE, "search_gateway_dashboard.css")
DASHBOARD_JS = os.path.join(HERE, "search_gateway_dashboard.js")
SETTINGS_PAGE = os.path.join(HERE, "search_gateway_settings.html")
SETTINGS_CSS = os.path.join(HERE, "search_gateway_settings.css")
SETTINGS_JS = os.path.join(HERE, "search_gateway_settings.js")
ENGINE_FILE = os.path.join(HERE, "search_gateway_engine.py")
SETTINGS_FILE = os.path.expanduser(os.environ.get("SEARCH_GATEWAY_SETTINGS", "~/.config/localllm/search-gateway.json"))
DB_PATH = os.path.expanduser(os.environ.get("WEBUI_DB", "~/.open-webui/webui.db"))  # only for /chat-types
DEBUG = os.environ.get("SEARCH_GATEWAY_DEBUG", "0") == "1"
SEARXNG_OVERRIDE = os.environ.get("SEARCH_GATEWAY_SEARXNG", "").strip()
REQUEST_TIMEOUT = 300  # seconds a search, page read or tool call may take in all

# SearXNG bangs at the start of the query: "!gh ", "!!wp " ... ("what is !important" is left alone)
LEAD_BANGS_RE = re.compile(r"^(?:!!?[\w.-]+\s+)+")
TIME_RANGES = {"day", "week", "month", "year"}
TOOL_METHODS = ("web_search", "plan_research", "read_page")
# Settings shown as "set / not set", never sent back to the page.
SECRET_RE = re.compile(r"(?:api_key|token|secret|password)$")
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}

_build_lock = threading.Lock()
_loaded = {"key": None, "tool": None, "module": None}
LOOP = asyncio.new_event_loop()


def log(text: str):
    print(f"[search-gateway] {text}", flush=True)


def run(coro, timeout: float = REQUEST_TIMEOUT):
    """Runs a coroutine on the gateway's event loop (from a request's thread) and waits for its result."""
    future = asyncio.run_coroutine_threadsafe(coro, LOOP)
    try:
        return future.result(timeout)
    except concurrent.futures.TimeoutError:
        future.cancel()
        raise TimeoutError(f"took longer than {timeout} s")


async def _call(fn, *args):
    """A plain function run on the event loop, so it sees the engine's state as the searches do."""
    return fn(*args)


# ---------------------------------------------------------------- the engine and its settings


def _mtime(path: str) -> int:
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return 0


def read_settings() -> dict:
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except ValueError as e:
        raise RuntimeError(f"{SETTINGS_FILE} is not valid JSON ({e}); fix it or save the settings page again")


def write_settings(data: dict):
    """Writes the settings file atomically, readable only by you."""
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(SETTINGS_FILE), prefix=".search-gateway.", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
            f.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, SETTINGS_FILE)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _tool():
    """The engine's Tools object with your settings; rebuilt when the engine file or the settings change. What the
    old object learned (services without usage reporting, last checks, pauses, each answer's counts, searches still
    running) carries over."""
    key = (_mtime(ENGINE_FILE), _mtime(SETTINGS_FILE))
    if key == _loaded["key"]:
        return _loaded["tool"]
    with _build_lock:
        if key == _loaded["key"]:
            return _loaded["tool"]
        module = _loaded["module"]
        if module is None or key[0] != _loaded["key"][0]:
            spec = importlib.util.spec_from_file_location("search_gateway_engine", ENGINE_FILE)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            version = re.search(r"^\s*version:\s*(\S+)", module.__doc__ or "", re.M)
            log(f"loaded the engine, version {version.group(1) if version else '?'}")
        tool = module.Tools()
        saved = read_settings()
        fields = set(getattr(tool.Valves, "model_fields", {}) or {})
        tool.valves = tool.Valves(**{k: v for k, v in saved.items() if k in fields})
        old = _loaded["tool"]
        if old is not None:
            for name in ("_no_real", "_checked", "_parallel_pause_until", "_state", "_in_flight", "_pages"):
                if hasattr(old, name):
                    setattr(tool, name, getattr(old, name))
        if DEBUG:
            tool.valves.debug = True
        if SEARXNG_OVERRIDE and tool.valves.searxng_url:
            tool.valves.searxng_url = SEARXNG_OVERRIDE
        _loaded.update(key=key, tool=tool, module=module)
        return tool


# ---------------------------------------------------------------- searching



def _plain(text) -> str:
    """Text without HTML tags or entities, on one line."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", str(text or ""))).split())


def _as_searxng(query: str, results: list, used: str, problems: list) -> dict:
    out, seen = [], set()
    for r in results:
        url = r.get("url") or ""
        if not url or url in seen:
            continue
        seen.add(url)
        item = {
            "url": url,
            "title": _plain(r.get("title")),
            "content": _plain(r.get("snippet")),
            "engine": used,
            "engines": [used],
            "category": "general",
        }
        if r.get("date"):
            item["publishedDate"] = str(r["date"])
        out.append(item)
    return {
        "query": query,
        "number_of_results": len(out),
        "results": out,
        "answers": [],
        "corrections": [],
        "infoboxes": [],
        "suggestions": [],
        "unresponsive_engines": [[p.split(":")[0], p.split(":", 1)[-1].strip()] for p in problems] if not out else [],
        "service": used,
        "problems": list(problems),
    }


async def search(params: dict) -> dict:
    query = " ".join(str(params.get("q", "")).split())
    if not query:
        return _as_searxng("", [], "", ["search-gateway: empty query"])

    # Bangs only mean something to SearXNG; the services would search for the text "!gh".
    query = LEAD_BANGS_RE.sub("", query).strip() or query

    tool = _tool()
    time_range = str(params.get("time_range", "")).strip().lower()
    time_range = time_range if time_range in TIME_RANGES else ""
    retry_after = [s for s in str(params.get("retry_after", "")).split(",") if s.strip()]
    skip = tool._retry_skip(retry_after) if retry_after else set()
    key = f"pi-{uuid.uuid4().hex}"
    turn = tool._turn({"message_id": key})
    turn["app"] = str(params.get("app") or "Pi")[:40]  # for the search log ("Protected Lookup" sets its own)
    started = time.time()
    try:
        results, used, problems = await tool._search_chain(query, time_range, "", turn, skip=skip, fresh=bool(retry_after))
    finally:
        tool._state.pop(key, None)
    if DEBUG:
        log(f"{query!r}{f' ({time_range})' if time_range else ''}{f' (retry, skipping {sorted(skip)})' if retry_after else ''}: "
            f"{len(results)} results via {used or 'nothing'} in {time.time() - started:.1f}s"
            + (f"; {'; '.join(problems)}" if problems else ""))
    return _as_searxng(query, results, used, problems)


async def read(params: dict) -> dict:
    """/read?url=...: a page's text through the engine's page reader (the local browser, then its fallback services).
    Used by Open WebUI's Protected Lookup tool, which only asks for pages on a chat type's allowed sites."""
    url = str(params.get("url", "")).strip()
    if not re.match(r"^https?://", url):
        return {"url": url, "error": "give a full http(s) URL"}
    tool = _tool()
    meta = {"message_id": f"read-{uuid.uuid4().hex}"}
    try:
        out = await tool.read_page(url, meta)
    finally:
        tool._state.pop(meta["message_id"], None)
    if out.startswith("Page: "):
        text = out.split("\n\n", 1)[1] if "\n\n" in out else ""
        # The note on parts and sections is for the model's read_page; the lookup cuts pages itself.
        return {"url": url, "text": re.sub(r"\n\n\[(?:Part \d+ of \d+|Sections after|Section #).*\]\s*$", "", text, flags=re.S)}
    return {"url": url, "error": out}


async def tool_call(method: str, data: dict) -> dict:
    """Open WebUI's "Search Gateway" tool: runs one of the engine's tool functions as Open WebUI would (the answer's
    ids for its per-answer limits, the user's last message), and returns its text plus the events it sent (status
    lines, citations) for the thin client to pass on."""
    if method not in TOOL_METHODS:
        raise ValueError(f"unknown tool function {method!r}; there are {', '.join(TOOL_METHODS)}")
    args = data.get("args") if isinstance(data.get("args"), dict) else {}
    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    meta = {k: str(meta[k]) for k in ("message_id", "chat_id") if meta.get(k)}
    events = []

    async def emitter(event):
        if isinstance(event, dict) and len(events) < 200:
            events.append(event)

    tool = _tool()
    kwargs = {"__metadata__": meta, "__event_emitter__": emitter}
    if method == "web_search":
        kwargs["__messages__"] = [{"role": "user", "content": str(data.get("user_text") or "")}]
        result = await tool.web_search(str(args.get("query") or ""), **kwargs)
    elif method == "plan_research":
        result = await tool.plan_research(str(args.get("plan") or ""), **kwargs)
    else:
        try:
            part = max(1, int(args.get("part") or 1))
        except (TypeError, ValueError):
            part = 1
        result = await tool.read_page(str(args.get("url") or ""), part, **kwargs)
    return {"result": result, "events": events}


# ---------------------------------------------------------------- usage dashboard

NAMES = {
    "parallel": "Parallel", "brightdata": "Bright Data", "tavily": "Tavily", "exa": "Exa", "you": "You.com (keyless)",
    "youapi": "You.com API", "browserbase": "Browserbase", "brave": "Brave",
}
UNITS = {
    "parallel": "$", "exa": "$", "youapi": "$", "brightdata": "requests", "tavily": "credits", "brave": "requests",
    "browserbase": "searches", "you": "searches",
}
HISTORY_DAYS = 30
LATEST_SEARCHES = 25


def _num(x):
    return round(float(x), 4) if isinstance(x, (int, float)) else x


def _next_cycle(start: str, day: int) -> str:
    """The first day of the cycle after the one that began on start (YYYY-MM-DD): day in the next month
    (its last day when the month is shorter)."""
    d = datetime.date.fromisoformat(start)
    y, m = (d.year, d.month + 1) if d.month < 12 else (d.year + 1, 1)
    return datetime.date(y, m, min(max(day, 1), calendar.monthrange(y, m)[1])).isoformat()


def _day_of(date: str) -> int:
    try:
        return datetime.date.fromisoformat(str(date).strip()).day
    except ValueError:
        return 1


def _retry_group(tool, name: str) -> list:
    for group in tool.valves.retry_groups.split(","):
        members = [m for m in (re.sub(r"[^a-z]", "", x.lower()) for x in group.split("/")) if m]
        members = [tool_module(tool).SERVICE_ALIASES.get(m, m) for m in members]
        if name in members:
            return members
    return [name]


def tool_module(tool):
    """The tool's module-level names (SIMPLE, METERED, _until...) as attributes."""
    return types.SimpleNamespace(**type(tool)._usage.__globals__)


def _service(tool, mod, name: str, usage: dict) -> dict:
    """One service's numbers, from the tool's own functions (the limits its searches use)."""
    v = tool.valves
    real = (usage.get("real") or {}).get(name)
    month_start = f"{usage.get('month') or mod._month()}-01"
    reserve = v.usage_reserve
    row = {"id": name, "name": NAMES.get(name) or mod.SIMPLE.get(name, {}).get("name", name), "extras": []}
    extras = row["extras"]
    cost, reserve_units, left_cap = 1.0, 0.0, None
    if name == "parallel":
        p = tool._parallel_usage(usage)
        cost = tool._parallel_search_cost()
        key, allowance, used = bool(v.parallel_api_key), v.parallel_monthly_budget_usd, p["usd"]
        if tool._extra_active():
            allowance += v.parallel_extra_credit_usd
            used += tool._extra_spent(usage)
        if v.parallel_extra_credit_usd > 0:
            extras.append(f"One-time credit: ${tool._extra_spent(usage):.3f} of ${v.parallel_extra_credit_usd:.2f} used"
                          + (f", until {v.parallel_extra_credit_expires.strip()}" if tool._extra_active() else " (expired)"))
        balance = tool._parallel_balance_left(usage)
        if balance is not None:
            extras.append(f"Credit left as Parallel reports it: ${balance:.2f}")
            left_cap = balance - reserve * cost
        extras.append(f"Monthly budget ${v.parallel_monthly_budget_usd:.2f}; {p.get('pages', 0)} pages read this cycle")
        searches, start = p.get("searches", 0), p["cycle"]
        resets = _next_cycle(start, _day_of(v.parallel_signup_date))
    elif name == "exa":
        cost = tool._exa_cost_estimate(usage)
        key, allowance = bool(v.exa_api_key), v.exa_monthly_credit_usd
        if tool._exa_extra_active():
            allowance += v.exa_extra_credit_usd
        used, reserve_units = allowance - tool._exa_left(usage), cost * reserve
        if v.exa_extra_credit_usd > 0:
            expires = v.exa_extra_credit_expires.strip()
            when = " (expired)" if not tool._exa_extra_active() else f", until {expires}" if expires else ""
            extras.append(f"One-time credit: ${tool._exa_extra_spent(usage):.3f} of ${v.exa_extra_credit_usd:.2f} used{when}")
        searches, start = usage.get("exa", 0), month_start
        resets = _next_cycle(start, 1)
    elif name == "youapi":
        cost = mod.YOUAPI_COST
        key, allowance, used, reserve_units = bool(v.you_api_key), v.you_api_credit_usd, tool._youapi_spent(usage), cost * reserve
        if real and "remaining" in real:
            left_cap = real["remaining"] - max(used - real.get("own_usd", 0.0), 0.0) - reserve_units
        expires = v.you_api_credit_expires.strip()
        extras.append("One-time credit" + (f", until {expires}" if expires else ", no expiry"))
        searches, start, resets = (usage.get("youapi") or {}).get("n", 0), "", ""
    elif name == "you":
        key, allowance, used = bool(v.you_mcp_url), v.you_daily_limit, tool._you_today(usage)
        searches, start = used, mod._day()
        resets = (datetime.date.fromisoformat(start) + datetime.timedelta(days=1)).isoformat()
        extras.append("Counted per day (UTC); no account or key")
    elif name in mod.SIMPLE:
        cost = tool._simple_cost(name, usage)
        key, allowance, used, reserve_units = bool(tool._key(name)), tool._simple_free(name, usage), usage.get(name, 0), cost * reserve
        if real and real.get("remaining") is not None:
            left_cap = real["remaining"] - max(used - real.get("own", 0), 0) - reserve_units
        if name == "geekflare" and v.geekflare_paid_monthly > 0:
            paid = mod._until(v.geekflare_paid_until)
            extras.append(f"Paid plan: {v.geekflare_paid_monthly:g} credits a month until {v.geekflare_paid_until.strip()}"
                          + (f" (then {v.geekflare_monthly_free:g})" if paid else " (ended)"))
        if name == "firecrawl" and v.firecrawl_extra_credits > 0:
            extras.append(f"One-time credits: {tool._fc_extra_spent(usage):g} of {v.firecrawl_extra_credits:g} used in all")
        searches = usage.get(f"{name}_n", 0)
        start = (usage.get("cycles") or {}).get(name) or month_start
        resets = _next_cycle(start, int(getattr(v, f"{name}_reset_day", 1) or 1))
        row["unit"] = mod.SIMPLE[name]["unit"]
    else:  # brightdata, tavily, brave, browserbase: a request limit (the Valve already keeps the reserve)
        key, allowance, used = bool(tool._key(name)), tool._limit(name), usage.get(name, 0)
        searches = used
        if name == "browserbase":
            start = (usage.get("cycles") or {}).get(name) or month_start
            resets = _next_cycle(start, int(v.browserbase_reset_day or 1))
            if v.use_browserbase_fetch:
                extras.append(f"Page fetches: {usage.get('browserbase_fetch', 0)} of {tool._limit('browserbase_fetch')}")
        else:
            start, resets = month_start, _next_cycle(month_start, 1)
        if real and real.get("limit"):
            extras.append(f"Limit reported by the service: {real['limit']:g}")
    left = allowance - used - reserve_units
    if left_cap is not None:
        left = min(left, left_cap)
    left = max(left, 0.0)
    has = key and tool._has_credit(name, usage)
    if not key:
        status = "no key"
    elif name == "parallel" and time.time() < getattr(tool, "_parallel_pause_until", 0):
        status = "paused"
    elif not has:
        status = "used up"
    elif left / (cost or 1) < reserve:
        status = "near reserve"
    else:
        status = "available"
    row.update({
        "key": key, "status": status, "unit": row.get("unit") or UNITS.get(name, ""),
        "used": _num(used), "allowance": _num(allowance), "reserve": _num(reserve_units), "left": _num(left),
        "searches_left": int(left / cost) if cost else None, "cost": _num(cost), "searches": searches,
        "cycle_start": start, "resets": resets,
        "source": "service" if real and name not in getattr(tool, "_no_real", set()) else "tool",
        "checked": _num(real.get("t")) if real else None,
        "retry_group": _retry_group(tool, name),
    })
    return row


def _history(entries: list) -> dict:
    today = datetime.date.today()
    days = [(today - datetime.timedelta(days=i)).isoformat() for i in range(HISTORY_DAYS - 1, -1, -1)]
    per_day = {d: {"total": 0, "cached": 0, "services": {}, "apps": {}} for d in days}
    for e in entries:
        d = per_day.get(datetime.date.fromtimestamp(e["t"]).isoformat())
        if d is None:
            continue
        service = ("cache" if e.get("cached") else e.get("service")) or "none"
        d["total"] += 1
        d["cached"] += bool(e.get("cached"))
        d["services"][service] = d["services"].get(service, 0) + 1
        app = e.get("app") or "?"
        d["apps"][app] = d["apps"].get(app, 0) + 1
    latest = [{k: e.get(k) for k in ("t", "app", "service", "results", "cached", "retry", "failed", "q") if k in e}
              for e in reversed(entries[-LATEST_SEARCHES:])]
    return {"days": [{"day": d, **per_day[d]} for d in days], "latest": latest}


def usage_report(tool) -> dict:
    """Everything /usage.json returns: each service in the active order, the cache, and the search history."""
    mod = tool_module(tool)
    usage = tool._usage()
    ranks = tool._ranks()
    last = len(ranks) + 1
    groups = [g.strip() for g in tool._active_order().split(",") if g.strip()]

    def share(name):
        try:
            return tool._share_left(name, usage)
        except Exception:
            return 0.0

    order = sorted(mod.METERED, key=lambda n: (ranks.get(n, last), -share(n), mod.METERED.index(n)))
    services = []
    for name in order:
        row = _service(tool, mod, name, usage)
        row["rank"] = ranks[name] + 1 if name in ranks else None
        row["group"] = groups[ranks[name]] if name in ranks and ranks[name] < len(groups) else ""
        services.append(row)

    v = tool.valves
    temporary = bool(v.temporary_service_order.strip()) and mod._until(v.temporary_order_until)
    entries = mod.read_log(time.time() - HISTORY_DAYS * 86400)
    history = _history(entries)
    today = history["days"][-1]
    cache = mod.cache_info(tool.valves.cache_hours)
    cache["hits_today"] = today["cached"]
    cache["hits_30_days"] = sum(d["cached"] for d in history["days"])
    return {
        "generated": round(time.time(), 1),
        "tool_version": (re.search(r"^\s*version:\s*(\S+)", mod.__doc__ or "", re.M) or [None, None])[1],
        "order": {
            "active": tool._active_order(),
            "temporary": temporary,
            "until": v.temporary_order_until.strip() if temporary else "",
            "normal": v.service_order,
        },
        "usage_reserve": v.usage_reserve,
        "services": services,
        "searxng": {"url": v.searxng_url, "on": bool(v.searxng_url)},
        "cache": cache,
        "log": {"words": bool(getattr(v, "log_search_words", False)), "searches_30_days": sum(d["total"] for d in history["days"]),
                "searches_today": today["total"]},
        "history": history,
    }


# ---------------------------------------------------------------- settings page


def settings_data(tool) -> dict:
    """Every setting with its description, default and value; secrets only as set / not set."""
    saved = read_settings()
    fields = []
    for name, field in tool.Valves.model_fields.items():
        value = getattr(tool.valves, name)
        kind = "bool" if isinstance(field.default, bool) else "int" if isinstance(field.default, int) else \
            "float" if isinstance(field.default, float) else "str"
        secret = bool(SECRET_RE.search(name))
        row = {"name": name, "type": kind, "description": field.description or "", "secret": secret,
               "default": None if secret else field.default, "changed": name in saved}
        if secret:
            row["set"] = bool(value)
        else:
            row["value"] = value
        fields.append(row)
    return {"file": SETTINGS_FILE, "fields": fields}


def save_settings(tool, data: dict):
    """{"values": {name: value}, "clear": [names]}: a secret left empty keeps its value; "clear" empties it.
    Checked by the engine's own settings model before anything is written; only values that differ from the
    defaults are kept in the file, so a later default applies to the rest."""
    values = data.get("values") if isinstance(data.get("values"), dict) else {}
    clear = {str(n) for n in data.get("clear") or []}
    fields = tool.Valves.model_fields
    unknown = sorted(set(values) - set(fields)) + sorted(clear - set(fields))
    if unknown:
        return 400, {"problems": [f"Unknown setting: {n}" for n in unknown]}
    merged = {k: v for k, v in read_settings().items() if k in fields}
    for name, value in values.items():
        if SECRET_RE.search(name) and value in ("", None):
            continue  # left empty: keep the key that is set
        merged[name] = value
    for name in clear:
        merged.pop(name, None)
    try:
        checked = tool.Valves(**merged)
    except Exception as e:  # pydantic's ValidationError lists each wrong field
        errors = getattr(e, "errors", None)
        problems = [f"{'.'.join(str(x) for x in err.get('loc', []))}: {err.get('msg')}" for err in errors()] if errors else [str(e)]
        return 400, {"problems": problems}
    out = {name: getattr(checked, name) for name in fields if getattr(checked, name) != fields[name].default}
    before = read_settings()
    changed = sorted(n for n in set(out) | set(before) if out.get(n) != before.get(n))
    write_settings(out)
    log(f"settings saved ({len(changed)} changed: {', '.join(changed) or 'none'})")  # names only, never values
    return 200, {"ok": True, "changed": changed}


# ---------------------------------------------------------------- HTTP


def _local(handler) -> bool:
    """A client on this computer, asking for 127.0.0.1 or localhost (not a name pointed at it: DNS rebinding)."""
    client = handler.client_address[0] if handler.client_address else ""
    host = (handler.headers.get("Host") or "").strip().lower()
    host_name = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
    return client in ("127.0.0.1", "::1", "::ffff:127.0.0.1") and host_name in LOCAL_HOSTS


def _same_origin(handler) -> bool:
    """No Origin (a program, not a browser) or this server's own: another website's page can't post here."""
    origin = (handler.headers.get("Origin") or "").strip().lower()
    host = (handler.headers.get("Host") or "").strip().lower()
    return not origin or origin == f"http://{host}"


def _json_post(handler, body: bytes):
    """The JSON object of a change request, or None after answering 403/400."""
    if "application/json" not in (handler.headers.get("Content-Type") or "") or not _same_origin(handler):
        handler._send(403, {"error": "Changes are only accepted as JSON from this server's own pages."})
        return None
    try:
        data = json.loads(body.decode("utf-8") or "{}")
        if not isinstance(data, dict):
            raise ValueError("not an object")
        return data
    except ValueError as e:
        handler._send(400, {"problems": [f"Bad request: {e}"]})
        return None


class Handler(BaseHTTPRequestHandler):
    server_version = "search-gateway/3.0"

    def _params(self) -> dict:
        return {k: v[-1] for k, v in parse_qs(urlparse(self.path).query).items()}

    def _send(self, status: int, data: dict):
        self._send_bytes(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _send_bytes(self, status: int, payload: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        if content_type.startswith("text/html"):
            # The pages load nothing from elsewhere and can't be framed by another site.
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                             "script-src 'self'; frame-ancestors 'none'")
            self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(payload)

    def _page(self, path: str, content_type: str = "text/html; charset=utf-8"):
        try:
            with open(path, "rb") as f:
                return self._send_bytes(200, f.read(), content_type)
        except OSError as e:
            return self._send(500, {"error": f"page missing: {e}"})

    def _fail(self, what: str, e: Exception):
        log(f"{what} failed: {type(e).__name__}: {e}")
        if DEBUG:
            traceback.print_exc()
        self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def _handle(self, body: bytes = b""):
        path = urlparse(self.path).path.rstrip("/")
        method = self.command
        if path in ("", "/healthz"):
            try:
                tool = run(_call(_tool))
                return self._send(200, {"status": "ok", "engine_loaded": True, "settings": os.path.exists(SETTINGS_FILE),
                                        "searxng": tool.valves.searxng_url})
            except Exception as e:
                return self._send(500, {"status": "error", "error": str(e)})
        if path == "/chat-types" or path.startswith("/chat-types/"):
            try:
                sys.path.insert(0, HERE) if HERE not in sys.path else None
                import chat_types_web  # services/chat_types_web.py: the protected chats' chat type page
            except Exception as e:
                return self._send(500, {"error": f"chat types page unavailable: {type(e).__name__}: {e}"})
            chat_types_web.handle(self, path, method, body, DB_PATH, PORT)
            return
        if path == "/dashboard":
            return self._page(DASHBOARD_FILE)
        if path == "/dashboard/search_gateway_dashboard.css":
            return self._page(DASHBOARD_CSS, "text/css; charset=utf-8")
        if path == "/dashboard/search_gateway_dashboard.js":
            return self._page(DASHBOARD_JS, "application/javascript; charset=utf-8")
        if path == "/usage.json":
            try:
                return self._send(200, run(_call(lambda: usage_report(_tool()))))
            except Exception as e:
                return self._fail("usage report", e)
        if path == "/settings" or path.startswith("/settings/"):
            if not _local(self):
                return self._send(403, {"error": "The settings page is only served to this computer (http://127.0.0.1)."})
            if method == "GET" and path == "/settings":
                return self._page(SETTINGS_PAGE)
            if method == "GET" and path == "/settings/search_gateway_settings.css":
                return self._page(SETTINGS_CSS, "text/css; charset=utf-8")
            if method == "GET" and path == "/settings/search_gateway_settings.js":
                return self._page(SETTINGS_JS, "application/javascript; charset=utf-8")
            if method == "GET" and path == "/settings/data":
                try:
                    return self._send(200, run(_call(lambda: settings_data(_tool()))))
                except Exception as e:
                    return self._fail("settings", e)
            if method == "POST" and path == "/settings/save":
                data = _json_post(self, body)
                if data is None:
                    return
                try:
                    status, result = run(_call(lambda: save_settings(_tool(), data)))
                    return self._send(status, result)
                except Exception as e:
                    return self._fail("saving the settings", e)
            return self._send(404, {"error": "unknown settings page"})
        if path.startswith("/tool/"):
            if method != "POST":
                return self._send(405, {"error": "POST a JSON object"})
            data = _json_post(self, body)
            if data is None:
                return
            try:
                return self._send(200, run(tool_call(path[len("/tool/"):], data)))
            except ValueError as e:
                return self._send(404, {"error": str(e)})
            except Exception as e:
                return self._fail(f"tool {path}", e)
        if path not in ("/search", "/read"):
            return self._send(404, {"error": "served: /search, /read, /tool/<name>, /dashboard, /usage.json, /settings, /healthz"})
        params = self._params()
        try:
            self._send(200, run(search(params) if path == "/search" else read(params)))
        except Exception as e:
            self._fail(path[1:], e)

    def do_GET(self):
        self._handle()

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self._handle(self.rfile.read(length) if length else b"")

    def log_message(self, fmt, *args):
        if DEBUG:
            log(fmt % args)


def main():
    threading.Thread(target=LOOP.run_forever, name="search-loop", daemon=True).start()
    try:
        run(_call(_tool))
    except Exception as e:
        log(f"warning: {e}. Searches will fail until this is fixed.")
    server = ThreadingHTTPServer((BIND, PORT), Handler)
    server.daemon_threads = True
    log(f"listening on http://{BIND}:{PORT}/search, dashboard /dashboard, settings /settings "
        f"(settings: {SETTINGS_FILE}{'' if os.path.exists(SETTINGS_FILE) else ', not made yet'})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
