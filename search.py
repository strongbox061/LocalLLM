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


def _load_settings():
    """Load settings from JSON file, return dict with defaults."""
    defaults = {
        "services": ["searxng"],
        "searxng_url": "http://localhost:8888",
        "timeout": 30,
        "max_results": 10,
        "api_keys": {},
    }
    if not os.path.exists(SETTINGS_FILE):
        return defaults
    try:
        with open(SETTINGS_FILE, "r") as f:
            user = json.load(f)
        defaults.update(user)
    except (json.JSONDecodeError, OSError):
        pass
    return defaults


def _save_settings(settings):
    """Save settings dict to JSON file."""
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=2)


def _strip_bangs(query):
    """Remove leading SearXNG bangs from query string."""
    return LEAD_BANGS_RE.sub("", query).strip()


def _get_bangs(query):
    """Extract leading bangs from query."""
    m = LEAD_BANGS_RE.search(query)
    return m.group(0).strip() if m else ""


def _read_file_safe(path):
    """Read a file safely, return None on any error."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


def _serve_static(handler, path, content_type):
    """Send a static file response if it exists."""
    content = _read_file_safe(path)
    if content is None:
        handler.send_error(404, "Not found")
        return False
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(content.encode("utf-8"))))
    handler.end_headers()
    handler.wfile.write(content.encode("utf-8"))
    return True


def _json_response(handler, code, data):
    """Send a JSON response."""
    body = json.dumps(data, default=str).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _call_engine(method, path, body=None):
    """Call the engine module and return (ok, result_or_error)."""
    if not os.path.exists(ENGINE_FILE):
        return False, "Engine module not found"
    try:
        spec = importlib.util.spec_from_file_location("engine", ENGINE_FILE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        func = getattr(mod, method, None)
        if func is None:
            return False, f"Engine method {method} not found"
        return func(path, body)
    except Exception as e:
        return False, str(e)


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
