"""
voice_router.py: picks the Kokoro voice from the language of the text, so answers are read aloud in the right
language without switching voices by hand.

Open WebUI sends its text-to-speech requests here (127.0.0.1:8881) instead of straight to Kokoro (8880):
  - voice "auto" (or none): the language of each piece of text (Open WebUI sends one sentence at a time) is
    detected with langdetect, and the voice comes from VOICES below. Text too short to detect reliably
    ("OK.", "7.3.4") reuses the language of the previous piece.
  - any other voice: passed through unchanged, so a voice picked by hand still works.
  - /v1/audio/voices: Kokoro's list with "auto" added first, so it appears in Open WebUI's voice picker.
  - everything else under /v1/ is forwarded to Kokoro unchanged.
Each speech response carries an X-Voice-Router header: "<language> -> <voice>".

Runs as the user service voice-router (services/voice-router.service), started and stopped with Kokoro by
start/kokoro.sh and stop/kokoro.sh. Uses Open WebUI's Python, which has langdetect.

Settings (environment variables, all optional):
  VOICE_ROUTER_PORT   port to listen on        (default 8881)
  KOKORO_URL          the Kokoro server        (default http://127.0.0.1:8880)
  VOICE_ROUTER_DEBUG  1 to log every choice    (default 0)
"""

import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from langdetect import DetectorFactory, detect_langs
from langdetect.lang_detect_exception import LangDetectException

# Voice per language. To change one, edit it here, then run stop/kokoro.sh and start/kokoro.sh.
VOICES = {"es": "em_alex", "en": "bm_george", "fr": "ff_siwis"}
DEFAULT_LANG = "en"
AUTO = "auto"

# Text shorter than this reuses the previous language.
MIN_WORDS = 3
MIN_LETTERS = 15

PORT = int(os.environ.get("VOICE_ROUTER_PORT", "8881"))
KOKORO = os.environ.get("KOKORO_URL", "http://127.0.0.1:8880").rstrip("/")
DEBUG = os.environ.get("VOICE_ROUTER_DEBUG", "0") == "1"

DetectorFactory.seed = 0  # same text, same answer
_lock = threading.Lock()
_last_lang = DEFAULT_LANG


def log(text: str):
    print(f"[voice-router] {text}", flush=True)


def pick_language(text: str) -> tuple[str, str]:
    """(language, how it was decided) for one piece of text."""
    global _last_lang
    letters = len(re.findall(r"[^\W\d_]", text))
    with _lock:
        if len(text.split()) < MIN_WORDS or letters < MIN_LETTERS:
            return _last_lang, "short, kept previous"
        try:
            ranked = [l for l in detect_langs(text) if l.lang in VOICES]
        except LangDetectException:
            ranked = []
        if not ranked:
            return _last_lang, "undetected, kept previous"
        _last_lang = ranked[0].lang
        return _last_lang, f"detected {ranked[0].prob:.2f}"


def forward(method: str, path: str, body: bytes | None, content_type: str | None):
    """(status, content type, body) of the same request sent to Kokoro."""
    headers = {"Content-Type": content_type} if content_type else {}
    req = Request(f"{KOKORO}{path}", data=body, method=method, headers=headers)
    try:
        with urlopen(req, timeout=300) as r:
            return r.status, r.headers.get("Content-Type", "application/octet-stream"), r.read()
    except HTTPError as e:
        return e.code, e.headers.get("Content-Type", "application/json"), e.read()


class Handler(BaseHTTPRequestHandler):
    server_version = "voice-router/1.0"

    def _send(self, status: int, content_type: str, body: bytes, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data: dict):
        self._send(status, "application/json", json.dumps(data).encode())

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _handle(self, method: str):
        path = self.path.split("?", 1)[0].rstrip("/")
        try:
            if method == "GET" and path == "/health":
                status, _, _ = forward("GET", "/health", None, None)
                return self._json(200 if status == 200 else 503,
                                  {"status": "ok" if status == 200 else "kokoro unavailable"})
            if not path.startswith("/v1/"):
                return self._json(404, {"error": "not found"})

            body = self._body() if method == "POST" else None
            if method == "GET" and path == "/v1/audio/voices":
                status, ctype, raw = forward("GET", self.path, None, None)
                if status == 200:
                    data = json.loads(raw)
                    data["voices"] = [{"id": AUTO, "name": f"{AUTO} (English / Spanish / French)"}] + [
                        v for v in data.get("voices", []) if (v.get("id") if isinstance(v, dict) else v) != AUTO
                    ]
                    data["default_voice"] = AUTO
                    return self._json(200, data)
                return self._send(status, ctype, raw)

            extra = None
            if method == "POST" and path == "/v1/audio/speech":
                payload = json.loads(body or b"{}")
                voice = str(payload.get("voice") or AUTO).strip()
                if voice == AUTO:
                    lang, how = pick_language(str(payload.get("input", "")))
                    payload["voice"] = VOICES[lang]
                    extra = {"X-Voice-Router": f"{lang} -> {payload['voice']}"}
                    if DEBUG:
                        log(f"{lang} ({how}) -> {payload['voice']}: {str(payload.get('input', ''))[:80]!r}")
                else:
                    extra = {"X-Voice-Router": f"as requested -> {voice}"}
                body = json.dumps(payload).encode()

            status, ctype, raw = forward(method, self.path, body, self.headers.get("Content-Type"))
            self._send(status, ctype, raw, extra)
        except (URLError, ConnectionError, TimeoutError) as e:
            self._json(502, {"error": f"Kokoro is not reachable at {KOKORO}: {e}"})
        except Exception as e:  # keep serving; report the problem to Open WebUI
            log(f"{method} {path} failed: {type(e).__name__}: {e}")
            self._json(500, {"error": f"{type(e).__name__}: {e}"})

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def log_message(self, fmt, *args):
        if DEBUG:
            log(fmt % args)


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    log(f"listening on http://127.0.0.1:{PORT}, forwarding to {KOKORO}; voices: {VOICES}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
