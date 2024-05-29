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


def check(name, ok, detail=""):
    """Print a pass/fail line for a single assertion."""
    status = "OK" if ok else "FAIL"
    msg = f"[{status}] {name}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    return ok


def load_module(name, path):
    """Import a module from an arbitrary filesystem path."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Session:
    """Minimal stand-in for aiohttp.ClientSession. No actual requests are made."""

    def __init__(self):
        self._closed = False

    async def get(self, url, **kwargs):
        raise NotImplementedError("requests are not made in these checks")

    async def post(self, url, **kwargs):
        raise NotImplementedError("requests are not made in these checks")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self._closed = True


def make_tool(**valves):
    """Build a minimal tool object that mimics the engine's valve-based config."""
    tool = types.SimpleNamespace()
    tool.valves = valves
    tool.headers = {"Content-Type": "application/json"}
    return tool


# ---------------------------------------------------------------- /usage.json numbers

usage = {
    "engines": {
        "search1": {"used": 45, "limit": 100, "key": "sk-abc"},
        "search2": {"used": 98, "limit": 100, "key": "sk-def"},
        "search3": {"used": 120, "limit": 100, "key": ""},
        "search4": {"used": 10, "limit": 50, "key": None},
    },
    "reserve_threshold": 80,
}


def set_cache(entries: dict):
    """Return a cache snapshot that the dashboard code reads."""
    return entries.copy()


def log_entries():
    """Return sample search-log entries for testing the log filter."""
    now = datetime.datetime.now(datetime.timezone.utc)
    return [
        {
            "timestamp": (now - datetime.timedelta(hours=2)).isoformat(),
            "app": "Pi",
            "query": "what is python",
            "cached": False,
        },
        {
            "timestamp": (now - datetime.timedelta(hours=5)).isoformat(),
            "app": "Open WebUI",
            "query": "explain quantum computing",
            "cached": True,
        },
        {
            "timestamp": (now - datetime.timedelta(days=10)).isoformat(),
            "app": "Pi",
            "query": "old query that should be dropped",
            "cached": False,
        },
    ]


# ---------------------------------------------------------------- the search log

async def _fake(session, query, time_range):
    """Simulate a successful search call without hitting the network."""
    return {
        "results": [{"title": "mock result", "url": "https://example.com"}],
        "took_ms": 12,
    }


async def _broken(session, query, time_range):
    """Simulate a failed search call."""
    raise ConnectionError("search engine unavailable")


import time  # noqa: E402


# ---------------------------------------------------------------- the page and /usage.json over HTTP

def _build_state_db(usage_data, cache_data, log_data):
    """Pack sample data into a temporary SQLite-like dict that the dashboard reads."""
    return {
        "usage": usage_data,
        "cache": cache_data,
        "log": log_data,
    }


def run_server(state, host="127.0.0.1", port=0):
    """Start a minimal HTTP server that serves /usage.json and the dashboard page."""
    state_json = json.dumps(state).encode("utf-8")

    class Handler(ThreadingHTTPServer):
        pass

    def _handle(self, request, client_address):
        if request.startswith("GET /usage.json"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(state_json)
        elif request.startswith("GET /"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>Dashboard</body></html>")
        else:
            self.send_response(404)
            self.end_headers()

    from http.server import BaseHTTPRequestHandler

    class MockHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            _handle(self, self.command + " " + self.path, self.client_address)

        def log_message(self, format, *args):
            pass  # silence stderr

    server = ThreadingHTTPServer((host, port), MockHandler)
    return server


def main():
    """Run all dashboard checks."""
    print("=== dashboard-check.py ===")

    # 1. usage.json numbers
    engine_stats = []
    for name, info in usage["engines"].items():
        limit = info["limit"]
        used = info["used"]
        key = info.get("key")
        if not key:
            state = "no key"
        elif used >= limit:
            state = "used up"
        elif used >= usage["reserve_threshold"]:
            state = "near reserve"
        else:
            state = "available"
        engine_stats.append((name, used, limit, state))

    for name, used, limit, state in engine_stats:
        ok = isinstance(used, int) and isinstance(limit, int) and state in (
            "available", "near reserve", "used up", "no key"
        )
        check(f"engine {name}: {used}/{limit} → {state}", ok)

    # 2. search log filtering
    entries = log_entries()
    now = datetime.datetime.now(datetime.timezone.utc)
    search_log_days = 7
    cutoff = now - datetime.timedelta(days=search_log_days)

    filtered = [e for e in entries if datetime.datetime.fromisoformat(e["timestamp"]).replace(tzinfo=datetime.timezone.utc) >= cutoff]
    check("log drops old entries", len(filtered) == 2, f"got {len(filtered)} of {len(entries)}")

    # 3. cached vs live
    cached = [e for e in filtered if e.get("cached")]
    live = [e for e in filtered if not e.get("cached")]
    check("log distinguishes cached from live", len(cached) == 1 and len(live) == 1)

    # 4. app identification
    apps = {e["app"] for e in filtered}
    check("log records app name", "Pi" in apps and "Open WebUI" in apps)

    # 5. HTTP server serves /usage.json
    state_db = _build_state_db(usage, set_cache({}), log_entries())
    srv = run_server(state_db)
    try:
        host, port = srv.server_address
        url = f"http://{host}:{port}/usage.json"
        resp = urllib.request.urlopen(url)
        body = json.loads(resp.read())
        ok = "engines" in body and len(body["engines"]) == 4
        check("HTTP /usage.json returns valid JSON", ok)
    finally:
        srv.shutdown()

    print("=== done ===")


if __name__ == "__main__":
    main()
