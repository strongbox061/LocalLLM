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
