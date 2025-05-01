"""
web_gateway.py: version 3.0.0
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


class SearchHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the search gateway."""

    def log_message(self, format, *args):
        if DEBUG:
            super().log_message(format, *args)

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        # Static files
        if path == "/dashboard.css":
            if _serve_static(self, DASHBOARD_CSS, "text/css"):
                return
        if path == "/dashboard.js":
            if _serve_static(self, DASHBOARD_JS, "application/javascript"):
                return
        if path == "/settings.css":
            if _serve_static(self, SETTINGS_CSS, "text/css"):
                return
        if path == "/settings.js":
            if _serve_static(self, SETTINGS_JS, "application/javascript"):
                return

        # Pages
        if path == "/" or path == "/dashboard":
            content = _read_file_safe(DASHBOARD_FILE)
            if content is not None:
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(content.encode("utf-8"))
            else:
                _json_response(self, 503, {"error": "Dashboard not found"})
            return

        if path == "/settings" or path == "/settings.html":
            content = _read_file_safe(SETTINGS_PAGE)
            if content is not None:
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self._cors_headers()
                self.end_headers()
                self.wfile.write(content.encode("utf-8"))
            else:
                _json_response(self, 503, {"error": "Settings page not found"})
            return

        # API endpoints
        if path == "/health":
            _json_response(self, 200, {"status": "ok"})
            return

        if path == "/settings":
            settings = _load_settings()
            # Don't expose API keys in GET
            safe = {k: v for k, v in settings.items() if k != "api_keys"}
            _json_response(self, 200, safe)
            return

        if path == "/chat-types":
            ok, result = _call_engine("get_chat_types", None)
            if ok:
                _json_response(self, 200, result)
            else:
                _json_response(self, 502, {"error": result})
            return

        # Search endpoint
        if path == "/search":
            params = parse_qs(parsed.query)
            q = params.get("q", [""])[0]
            if not q:
                _json_response(self, 400, {"error": "Missing 'q' parameter"})
                return
            max_results = int(params.get("max_results", ["10"])[0])
            bangs = _get_bangs(q)
            clean_q = _strip_bangs(q)
            settings = _load_settings()
            search_opts = {
                "query": clean_q,
                "max_results": min(max_results, settings.get("max_results", 10)),
                "bangs": bangs,
            }
            ok, result = _call_engine("search", search_opts)
            if ok:
                _json_response(self, 200, result)
            else:
                _json_response(self, 502, {"error": result})
            return

        _json_response(self, 404, {"error": "Not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        # Read body
        content_length = int(self.headers.get("Content-Length", 0))
        body_raw = self.rfile.read(content_length) if content_length > 0 else b"{}"
        try:
            body = json.loads(body_raw)
        except json.JSONDecodeError:
            _json_response(self, 400, {"error": "Invalid JSON"})
            return

        if path == "/settings":
            settings = _load_settings()
            settings.update(body)
            _save_settings(settings)
            _json_response(self, 200, {"status": "saved"})
            return

        if path == "/search":
            q = body.get("q", "")
            if not q:
                _json_response(self, 400, {"error": "Missing 'q' parameter"})
                return
            max_results = body.get("max_results", 10)
            bangs = _get_bangs(q)
            clean_q = _strip_bangs(q)
            settings = _load_settings()
            search_opts = {
                "query": clean_q,
                "max_results": min(int(max_results), settings.get("max_results", 10)),
                "bangs": bangs,
            }
            ok, result = _call_engine("search", search_opts)
            if ok:
                _json_response(self, 200, result)
            else:
                _json_response(self, 502, {"error": result})
            return

        _json_response(self, 404, {"error": "Not found"})


def main():
    """Start the search gateway server."""
    server = ThreadingHTTPServer((BIND, PORT), SearchHandler)
    print(f"Search gateway running on http://{BIND}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("\nSearch gateway stopped.")


if __name__ == "__main__":
    main()
