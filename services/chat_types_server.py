"""
chat_types_server.py: the "Chat types" page (services/chat_types_web.py) on the main PC in split mode, where the search
gateway, which serves it in single mode, runs on the helper PC (README, "Two-PC setup"). Started in the foreground by
`./localllm.sh chat-types`; Ctrl+C ends it.

The page is the same: it edits ~/.config/localllm/chat-types/<id>.json here and runs install/chat-types.sh, which in
split mode sends the files to the helper and builds the presets there. The presets', Knowledge bases' and accounts'
status comes from the helper's Open WebUI database (windows/tasks.ps1 chat-types-status, over SSH).

Listens on 127.0.0.1 only, on the search gateway's port (8090), so the page's address is the same in both modes;
chat_types_web.py's own checks (this computer only, same-origin changes) apply as in the gateway.

Usage: python3 chat_types_server.py [port]
"""

import json
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
import chat_types_web  # noqa: E402

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
STATUS_CMD = ["bash", "-c", 'source "$1/lib/common.sh" && source "$1/lib/helper.sh" && helper_task chat-types-status',
              "status", str(REPO)]
_cache = {"at": 0.0, "value": None}


def helper_status() -> dict:
    """db_status() as the helper's Open WebUI has it (a few seconds' cache: the page reads it once per load)."""
    if _cache["value"] is not None and time.time() - _cache["at"] < 5:
        return _cache["value"]
    p = subprocess.run(STATUS_CMD, capture_output=True, text=True, timeout=60)
    lines = [ln for ln in p.stdout.splitlines() if ln.strip().startswith("{")]
    if p.returncode != 0 or not lines:
        raise RuntimeError((p.stderr or p.stdout).strip().splitlines()[-1] if (p.stderr or p.stdout).strip()
                           else f"the helper's status failed (exit {p.returncode})")
    value = json.loads(lines[-1])
    _cache.update(at=time.time(), value=value)
    return value


chat_types_web.STATUS_SOURCE = helper_status

OTHER_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{title}</title>
<style>body{{font:15px/1.5 system-ui,sans-serif;max-width:640px;margin:40px auto;padding:0 16px;color:#222;
background:#fff}}code{{background:#f1f1f1;padding:1px 5px;border-radius:4px}}@media(prefers-color-scheme:dark){{
body{{color:#ddd;background:#1b1b1b}}code{{background:#333}}}}</style></head><body>
<h1>{title}</h1><p>{text}</p><p><a href="/chat-types">Chat types</a></p></body></html>"""
OTHERS = {
    "/dashboard": ("Search usage dashboard", "The search gateway runs on the helper PC: <code>./localllm.sh status</code> "
                   "prints its dashboard's address."),
    "/settings": ("Search settings", "The search gateway runs on the helper PC. Close this page's command (Ctrl+C), "
                  "then run <code>./localllm.sh settings</code> and open the address it prints."),
}


class Handler(BaseHTTPRequestHandler):
    server_version = "chat-types/1.0"

    def _send(self, status: int, data: dict):
        self._send_bytes(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _send_bytes(self, status: int, payload: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        if content_type.startswith("text/html"):
            # As the search gateway's pages: nothing loaded from elsewhere, never framed by another site.
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                             "script-src 'self'; frame-ancestors 'none'")
            self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(payload)

    def _handle(self, body: bytes = b""):
        path = urlparse(self.path).path.rstrip("/")
        if path == "":
            self.send_response(302)
            self.send_header("Location", "/chat-types")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path in OTHERS and self.command == "GET":
            title, text = OTHERS[path]
            return self._send_bytes(200, OTHER_PAGE.format(title=title, text=text).encode("utf-8"),
                                    "text/html; charset=utf-8")
        if not chat_types_web.handle(self, path, self.command, body, "", PORT):
            self._send(404, {"error": "served: /chat-types"})

    def do_GET(self):
        self._handle()

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1_000_000:
            return self._send(413, {"error": "too large"})
        self._handle(self.rfile.read(length) if length else b"")

    def log_message(self, fmt, *args):
        pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Chat types: http://127.0.0.1:{PORT}/chat-types (Ctrl+C ends it)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
