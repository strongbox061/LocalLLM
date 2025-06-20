"""
audio_router.py: picks the Kokoro voice from the language of the text, so answers are read aloud in the right
language without switching voices by hand.

Open WebUI sends its text-to-speech requests here (127.0.0.1:8881) instead of straight to Kokoro (8880):
  - voice "auto" (or none): the language of each piece of text is detected with langdetect, and the
    voice comes from VOICES below. Text too short reuses the language of the previous piece.
  - any other voice: passed through unchanged.
  - /v1/audio/voices: Kokoro's list with "auto" added first.
  - everything else under /v1/ is forwarded to Kokoro unchanged.
Each speech response carries an X-Voice-Router header: "<language> -> <voice>".
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

DetectorFactory.seed = 0

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
    """Handle Open WebUI TTS requests, resolve 'auto' voice, and forward to Kokoro."""

    def log_message(self, format, *args):
        if DEBUG:
            log(f"{self.client_address[0]} - {format % args}")

    def _respond(self, status: int, headers: dict | None, body: bytes):
        self.send_response(status)
        if headers:
            for key, value in headers.items():
                if key.lower() not in ("transfer-encoding",):
                    self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _get_voice(self, path: str, body: bytes | None) -> tuple[str, str, bytes | None]:
        """Parse the request body to resolve voice, return (lang, resolved_voice, new_body)."""
        if body is None:
            return DEFAULT_LANG, VOICES[DEFAULT_LANG], None

        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return DEFAULT_LANG, VOICES[DEFAULT_LANG], body

        voice = data.get("voice", "") or ""
        text = data.get("text", "") or ""

        if voice == AUTO or not voice:
            lang, resolved_voice = pick_language(text)
            data["voice"] = resolved_voice
            return lang, resolved_voice, json.dumps(data).encode()

        # Explicit voice — detect language for the header but don't change it.
        lang, _ = pick_language(text)
        return lang, voice, body

    def _handle_v1(self, method: str, path: str):
        """Handle /v1/* routes."""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else None
        content_type = self.headers.get("Content-Type")

        # /v1/audio/voices — inject "auto" into Kokoro's voice list.
        if path == "/v1/audio/voices":
            status, headers, response_body = forward(method, path, body, content_type)
            if status == 200 and response_body:
                try:
                    voices = json.loads(response_body)
                    if isinstance(voices, list) and AUTO not in voices:
                        voices.insert(0, AUTO)
                    response_body = json.dumps(voices).encode()
                except (json.JSONDecodeError, TypeError):
                    pass
            self._respond(status, headers, response_body)
            return

        # /v1/audio/speech — resolve "auto" voice, forward to Kokoro.
        if path == "/v1/audio/speech":
            lang, resolved_voice, new_body = self._get_voice(path, body)
            status, headers, response_body = forward(method, path, new_body, content_type)
            if status == 200:
                headers["X-Voice-Router"] = f"{lang} -> {resolved_voice}"
            self._respond(status, headers, response_body)
            return

        # All other /v1/* routes — forward as-is.
        forward(method, path, body, content_type)

    def _route(self, method: str):
        path = self.path.split("?")[0]  # strip query string
        if path.startswith("/v1/"):
            self._handle_v1(method, path)
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PUT(self):
        self._route("PUT")

    def do_DELETE(self):
        self._route("DELETE")


def main():
    """Start the voice router server."""
    log(f"Starting voice router on port {PORT}, forwarding to {KOKORO}")
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("Shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
