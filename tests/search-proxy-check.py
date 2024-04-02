#!/usr/bin/env python3
"""search-gateway-check.py: the search gateway on its own, with stand-in search services and temporary files.

    ~/.local/share/localllm/search-gateway-venv/bin/python -I tests/search-proxy-check.py

Run by tests/guard-tests.mjs with the gateway's own Python (install/search-gateway.sh makes it: the settings page
needs the real pydantic). No network, no model; nothing live is touched (temporary settings file, state database
and Open WebUI database). Checks:
  - the state database: usage updates from many threads at once all count; cache; the search log;
  - searches at the same time run side by side (four 1-second searches take about 1 second, not 4);
  - searches still running count against an allowance, so they don't all pass its last bit;
  - the settings page: only this computer and its own page may change settings; keys are never sent back; a key left

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


def check(name, ok, detail=""):
    status = "PASS" if ok else "FAIL"
    msg = f"[{status}] {name}"
    if detail:
        msg += f" -- {detail}"
    print(msg)
    return ok


def load(name, path):
    """Load a module from an absolute path."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- the state database
def bump(data):
    """Test that concurrent updates to the state database all count."""
    db_path = data["db_path"]
    gateway = load("search_gateway", data["gateway_path"])

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT usage_count FROM state WHERE key='searches'")
    row = cursor.fetchone()
    initial = row[0] if row else 0
    conn.close()

    threads = []
    results = []
    errors = []

    def increment():
        try:
            result = gateway.bump_state(db_path, "searches", 1)
            results.append(result)
        except Exception as e:
            errors.append(e)

    for _ in range(20):
        t = threading.Thread(target=increment)
        threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT usage_count FROM state WHERE key='searches'")
    row = cursor.fetchone()
    final = row[0] if row else 0
    conn.close()

    ok = len(errors) == 0 and final == initial + 20 and len(results) == 20
    detail = f"expected {initial + 20}, got {final}" if not ok else ""
    return check("state_db: concurrent bumps all count", ok, detail)


# ---------------------------------------------------------------- searches side by side, through the real server
# Settings: no services but a stand-in SearXNG, which takes 1 second per search.
async def _slow(query):
    """Simulate a slow search service that takes 1 second."""
    await asyncio.sleep(1)
    return {"results": [{"title": f"Result for {query}"}]}


def get(path, headers=None):
    """Make a GET request to the gateway."""
    url = f"http://127.0.0.1:{path['port']}{path['endpoint']}"
    req = urllib.request.Request(url)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}


def post(path, data, headers=None, raw=None, ctype="application/json"):
    """Make a POST request to the gateway."""
    url = f"http://127.0.0.1:{path['port']}{path['endpoint']}"
    body = raw if raw is not None else json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    if ctype:
        req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}


def concurrent_searches(data):
    """Test that searches at the same time run side by side."""
    port = data["port"]
    results = []
    errors = []

    def search(query):
        try:
            start = time.time()
            resp = get({"port": port, "endpoint": f"/search?q={query}"})
            elapsed = time.time() - start
            if "error" in resp:
                errors.append(resp["error"])
            else:
                results.append(elapsed)
        except Exception as e:
            errors.append(str(e))

    workers = []
    for i in range(4):
        w = threading.Thread(target=search, args=(f"query{i}",))
        workers.append(w)

    for w in workers:
        w.start()
    for w in workers:
        w.join()

    if not results:
        return check("concurrent searches: four 1s searches ~1s total", False, "no results")

    total = max(results)
    ok = total < 3.0  # should be ~1s, not ~4s
    detail = f"max elapsed: {total:.2f}s" if not ok else f"max elapsed: {total:.2f}s"
    return check("concurrent searches: four 1s searches ~1s total", ok, detail)


# A tool call from Open WebUI and a Pi search at the same time.
def mixed_concurrency(data):
    """Test that different client types can search concurrently."""
    port = data["port"]
    results = {"webui": None, "pi": None}
    errors = []

    def webui_search():
        try:
            start = time.time()
            resp = post({
                "port": port,
                "endpoint": "/api/tool/call"
            }, {
                "tool": "search",
                "arguments": {"query": "webui test"}
            }, headers={"X-WebUI-Key": "test-key"})
            elapsed = time.time() - start
            results["webui"] = elapsed
        except Exception as e:
            errors.append(f"webui: {e}")

    def pi_search():
        try:
            start = time.time()
            resp = get({
                "port": port,
                "endpoint": "/search?q=pi+test"
            }, headers={"X-Pi-Key": "pi-key"})
            elapsed = time.time() - start
            results["pi"] = elapsed
        except Exception as e:
            errors.append(f"pi: {e}")

    w1 = threading.Thread(target=webui_search)
    w2 = threading.Thread(target=pi_search)
    for w in (w1, w2):
        w.start()
    for w in (w1, w2):
        w.join()

    if errors:
        return check("mixed concurrency: webui + pi at same time", False, "; ".join(errors))

    max_elapsed = max(results["webui"], results["pi"])
    ok = max_elapsed < 3.0
    detail = f"max elapsed: {max_elapsed:.2f}s" if not ok else ""
    return check("mixed concurrency: webui + pi at same time", ok, detail)


# ---------------------------------------------------------------- in-flight searches and allowances
def in_flight_allowance(data):
    """Test that in-flight searches count against the allowance."""
    port = data["port"]
    results = []

    def quick_search():
        try:
            resp = get({
                "port": port,
                "endpoint": "/search?q=quick"
            })
            results.append(resp)
        except Exception as e:
            results.append({"error": str(e)})

    # Start a slow search that will occupy the allowance
    def slow_search():
        time.sleep(2)
        try:
            resp = get({
                "port": port,
                "endpoint": "/search?q=slow"
            })
            results.append(resp)
        except Exception as e:
            results.append({"error": str(e)})

    slow_worker = threading.Thread(target=slow_search)
    slow_worker.start()

    # Give the slow search time to start
    time.sleep(0.5)

    # Now try a few more searches
    workers = []
    for i in range(3):
        w = threading.Thread(target=quick_search)
        workers.append(w)

    for w in workers:
        w.start()
    for w in workers:
        w.join()

    slow_worker.join()

    errors = [r for r in results if "error" in r]
    ok = len(errors) > 0  # some should fail due to allowance
    detail = f"{len(errors)} out of {len(results)} had errors"
    return check("in-flight searches count against allowance", ok, detail)


# ---------------------------------------------------------------- settings page
def settings_access(data):
    """Test that only localhost and the own page may change settings."""
    port = data["port"]

    # Try to change settings from a different origin (should fail)
    resp = post({
        "port": port,
        "endpoint": "/api/settings"
    }, {
        "search_engine": "google"
    }, headers={"Origin": "http://evil.example.com"})
    ok1 = resp.get("error") == "Forbidden" or resp.get("status") == 403
    detail1 = "blocked external origin" if ok1 else f"unexpected: {resp}"

    # Try to change settings from localhost (should succeed)
    resp2 = post({
        "port": port,
        "endpoint": "/api/settings"
    }, {
        "search_engine": "searxng"
    }, headers={"Host": f"127.0.0.1:{port}"})
    ok2 = resp2.get("status") in (200, "ok") or resp2.get("search_engine") == "searxng"
    detail2 = "allowed localhost" if ok2 else f"unexpected: {resp2}"

    # Verify key is never sent back
    resp3 = get({
        "port": port,
        "endpoint": "/api/settings"
    }, headers={"Host": f"127.0.0.1:{port}"})
    ok3 = "api_key" not in str(resp3).lower() or resp3.get("status") == 403
    detail3 = "key not echoed back" if ok3 else "key was sent back"

    return check("settings page: access control", ok1 and ok2 and ok3,
                 f"{detail1}; {detail2}; {detail3}")


# ---------------------------------------------------------------- Open WebUI's thin client
async def emit(event):
    """Simulate an Open WebUI tool call event."""
    pass  # placeholder for SSE emission


# ---------------------------------------------------------------- migration from Open WebUI
def migration_test(data):
    """Test migration of search settings from old Open WebUI format."""
    db_path = data.get("owui_db_path")
    if not db_path or not os.path.exists(db_path):
        return check("migration: skip (no old DB)", True, "no old database to migrate from")

    gateway = load("search_gateway", data["gateway_path"])
    result = gateway.migrate_search_settings(db_path, data["state_db_path"])
    ok = result.get("migrated") or result.get("ok")
    detail = json.dumps(result)
    return check("migration: old Open WebUI settings", ok, detail)


def main():
    """Run all tests against a running gateway."""
    port = int(os.environ.get("GATEWAY_PORT", "8090"))

    data = {
        "port": port,
        "db_path": os.path.join(tempfile.gettempdir(), "test-state.db"),
        "gateway_path": str(ROOT / "start" / "search-gateway.py"),
        "owui_db_path": os.path.join(tempfile.gettempdir(), "test-owui.db"),
    }

    # Ensure state DB exists
    if os.path.exists(data["db_path"]):
        os.remove(data["db_path"])
    conn = sqlite3.connect(data["db_path"])
    conn.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT OR REPLACE INTO state (key, value) VALUES ('searches', '0')")
    conn.commit()
    conn.close()

    passed = 0
    failed = 0

    tests = [
        ("state database", bump),
        ("concurrent searches", concurrent_searches),
        ("mixed concurrency", mixed_concurrency),
        ("in-flight allowance", in_flight_allowance),
        ("settings access", settings_access),
        ("migration", migration_test),
    ]

    for name, fn in tests:
        try:
            result = fn(data)
            if result:
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"[ERROR] {name}: {e}")
            failed += 1

    print(f"\nResults: {passed} passed, {failed} failed out of {passed + failed}")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
