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

STATUS_CMD = ["bash", "-c",
              'source "$1/lib/common.sh" && source "$1/lib/helper.sh" && helper_task chat-types-status',
              "status", str(REPO)]
_cache = {"at": 0.0, "value": None}


def helper_status() -> dict:
    """db_status() as the helper's Open WebUI has it (a few seconds' cache)."""
    if _cache["value"] is not None and time.time() - _cache["at"] < 5:
        return _cache["value"]
    p = subprocess.run(STATUS_CMD, capture_output=True, text=True, timeout=60)
    lines = [ln for ln in p.stdout.splitlines() if ln.strip().startswith("{")]
    if p.returncode != 0 or not lines:
        raise RuntimeError((p.stderr or p.stdout).strip().splitlines()[-1]
                           if (p.stderr or p.stdout).strip()
                           else f"the helper's status failed (exit {p.returncode})")
    value = json.loads(lines[-1])
    _cache.update(at=time.time(), value=value)
    return value


OTHER_PAGE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>{title}</title></head>
<body><h1>{title}</h1><p>{message}</p></body>
</html>"""

OTHERS = {
    "chat-types": "Chat types",
    "tasks": "Tasks",
    "settings": "Settings",
}


class Handler(BaseHTTPRequestHandler):
    """HTTP handler for the chat-types server."""

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, title: str, message: str = ""):
        html = OTHER_PAGE.format(title=title, message=message).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/status":
            status = helper_status()
            self._send_json(status)
        elif parsed.path == "/health":
            self._send_json({"ok": True})
        else:
            name = OTHERS.get(parsed.path.lstrip("/"), "Chat types")
            self._send_html(name)

    def log_message(self, format, *args):
        """Suppress default stderr logging."""
        pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Chat types server listening on 127.0.0.1:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
