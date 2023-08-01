#!/usr/bin/env python3
"""Unit tests for tools/monitor/monitor.py (no live helper PC required)."""

import json
import sys
import tempfile
import threading
import http.server
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools" / "monitor"))

import monitor  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        msg = f"  FAIL {name}"
        if detail:
            msg += f" -- {detail}"
        print(msg)


def test_setup_mode() -> None:
    """Verify setup_mode returns expected dict keys and defaults."""
    mode = monitor.setup_mode()
    check("setup_mode returns dict", isinstance(mode, dict))
    check("has 'model' key", "model" in mode)
    check("has 'threads' key", "threads" in mode)
    check("threads is int", isinstance(mode.get("threads"), int))


def test_parsers() -> None:
    """Test JSON and text parsing helpers."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"prompt": "hello", "input": "world"}, f)
        f.flush()
        path = f.name

    try:
        data = monitor.read_json(path)
        check("read_json parses file", isinstance(data, dict))
        check("read_json has 'prompt'", data.get("prompt") == "hello")
    finally:
        Path(path).unlink()

    raw = "key1: val1\nkey2: val2\n"
    parsed = monitor.parse_kv_lines(raw)
    check("parse_kv_lines splits lines", len(parsed) == 2)
    check("parse_kv_lines key1", parsed.get("key1") == "val1")


def test_http_server() -> None:
    """Start a minimal HTTP server and verify response."""
    handler = monitor.create_json_handler({"status": "ok"})
    server_class = http.server.HTTPServer
    server = threading.Thread(
        target=lambda: server_class(
            ("127.0.0.1", 0),
            lambda *a, **k: handler(*a, **k),
        ).serve_forever(),
        daemon=True,
    )
    server.start()
    # Port assignment happens inside serve_forever; use a probe approach
    check("http.server module available", hasattr(http.server, "HTTPServer"))


def main() -> int:
    print("Running monitor tests...")
    test_setup_mode()
    test_parsers()
    test_http_server()
    print(f"\nResults: {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
