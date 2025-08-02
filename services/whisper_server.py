"""
whisper_server.py: speech-to-text for Open WebUI on the helper PC (README, "Voice in Open WebUI"), an
OpenAI-compatible /v1/audio/transcriptions with faster-whisper.

Why a service of its own: inside Open WebUI's process the same model on the same GPU took 2-3 times longer per
recording (it shares the process with the document search models), and it loaded only on the first recording after
each start (about 9 s more). Here the model is loaded before the port opens, so the first recording is as fast as
the others, and one recording is transcribed at a time (one GPU).

Language: a recording with a language given (Open WebUI sends one only when it's set in your settings) uses it;
otherwise the language is detected among LANGUAGES only (the ones the voice router reads aloud,
services/voice_router.py), then the recording is transcribed in it. A short or unclear clip can't come back in a
language nobody here speaks.

Runs as the helper's Windows service localllm-whisper (windows/common.ps1 Get-ServiceSpec), with Open WebUI's Python
(it has faster-whisper and CUDA torch). Open WebUI uses it in split mode: open-webui/topology.py sets its
speech-to-text engine to "openai" at http://127.0.0.1:8882/v1.

  GET  /health                     200 once the model is loaded
  GET  /v1/models                  the model's name
  POST /v1/audio/transcriptions    multipart: file, optional language, response_format (json, text, verbose_json)

Settings (environment variables):
  WHISPER_PORT           port to listen on                     (default 8882)
  WHISPER_MODEL          faster-whisper model name             (default large-v3-turbo)
  WHISPER_MODEL_DIR      where models are kept                 (default: Hugging Face's cache)
  WHISPER_DEVICE         cuda or cpu                           (default cuda)
  WHISPER_COMPUTE_TYPE   int8, float16...                      (default int8: Pascal cards have no fast FP16)
  WHISPER_LANGUAGES      languages to pick from, comma-separated  (default es,en,fr; empty = any)
  WHISPER_BEAM_SIZE      1 = fastest, 5 = Open WebUI's own     (default 5)
"""

import json
import os
import sys
import tempfile
import threading
import time
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if os.name == "nt":
    # On Windows, CUDA's libraries come with torch; loading it first lets CTranslate2 find them (without it, the first
    # transcription on the GPU hangs).
    import torch  # noqa: F401

import numpy as np
from faster_whisper import WhisperModel, decode_audio
from faster_whisper.audio import pad_or_trim
from faster_whisper.vad import VadOptions, collect_chunks, get_speech_timestamps

PORT = int(os.environ.get("WHISPER_PORT", "8882"))
MODEL = os.environ.get("WHISPER_MODEL", "large-v3-turbo")
MODEL_DIR = os.environ.get("WHISPER_MODEL_DIR") or None
DEVICE = os.environ.get("WHISPER_DEVICE", "cuda")
COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")
LANGUAGES = [l.strip() for l in os.environ.get("WHISPER_LANGUAGES", "es,en,fr").split(",") if l.strip()]
BEAM_SIZE = int(os.environ.get("WHISPER_BEAM_SIZE", "5"))
MAX_UPLOAD = 100 * 1024 * 1024

_lock = threading.Lock()
_model: WhisperModel | None = None
_reuse: "EncoderReuse | None" = None


def log(text: str):
    print(f"[whisper] {text}", flush=True)


class EncoderReuse:
    """Runs Whisper's encoder (most of the work: about 0.9 s per clip on a 6 GB Pascal GPU) once per clip instead of twice.

    Detecting the language needs the encoder's output for the clip's first 30 s, and faster-whisper's transcribe()
    computes the same output again for its first window. pick_language() computes that window exactly as transcribe()
    will (same features, same padding) and leaves the result here; the next encode() of an identical window takes it
    instead of running the encoder. Any other window, or a faster-whisper that builds it differently, just runs the
    encoder as usual."""

    def __init__(self, model: WhisperModel):
        self.encode = model.encode
        self.kept = None
        self.reused = False
        model.encode = self.reuse_or_encode

    def keep(self, window, output):
        self.kept = (window, output)

    def reuse_or_encode(self, window):
        kept, self.kept = self.kept, None
        if kept is not None and kept[0].shape == window.shape and np.array_equal(kept[0], window):
            self.reused = True
            return kept[1]
        return self.encode(window)


def speech_only(audio):
    """The clip without its silences (faster-whisper's own VAD filter, as transcribe(vad_filter=True) uses it)."""
    chunks = get_speech_timestamps(audio, VadOptions())
    return np.concatenate(collect_chunks(audio, chunks)[0]) if chunks else audio[:0]


def pick_language(audio) -> str | None:
    """The most likely of LANGUAGES for the clip (None: let Whisper choose, when LANGUAGES is empty)."""
    if not LANGUAGES:
        return None
    fe = _model.feature_extractor
    features = fe(audio)
    # transcribe()'s first window: up to 30 s of the clip's frames (all but the last), padded to 30 s.
    window = pad_or_trim(features[:, : min(fe.nb_max_frames, features.shape[-1] - 1)])
    output = _reuse.encode(window)
    _reuse.keep(window, output)
    probs = {token[2:-2]: p for token, p in _model.model.detect_language(output)[0]}
    return max(LANGUAGES, key=lambda l: probs.get(l, 0.0))


def transcribe(path: str, language: str | None) -> dict:
    t0 = time.monotonic()
    audio = decode_audio(path, sampling_rate=16000)
    secs = len(audio) / 16000
    speech = speech_only(audio)
    if len(speech) < 1600:  # under 0.1 s of speech: nothing said
        log(f"{secs:.1f} s of audio with no speech")
        return {"text": "", "language": language or "", "duration": round(secs, 2)}
    with _lock:
        _reuse.reused = False
        try:
            lang = language or pick_language(speech)
            segments, info = _model.transcribe(speech, language=lang, beam_size=BEAM_SIZE, vad_filter=False)
            text = "".join(s.text for s in segments).strip()
        finally:
            _reuse.kept = None
    how = "given" if language else "detected, encoder reused" if _reuse.reused else "detected"
    log(f"{secs:.1f} s of audio in {time.monotonic() - t0:.2f} s, {info.language} ({how}), {len(text)} characters")
    return {"text": text, "language": info.language, "duration": round(secs, 2)}


def parse_form(content_type: str, body: bytes) -> dict:
    """multipart/form-data -> {name: bytes}."""
    msg = BytesParser(policy=HTTP).parsebytes(f"Content-Type: {content_type}\r\n\r\n".encode() + body)
    if not msg.is_multipart():
        raise ValueError("expected multipart/form-data")
    return {part.get_param("name", header="content-disposition"): part.get_payload(decode=True) or b""
            for part in msg.iter_parts()}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def send(self, status: int, body, content_type="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def error(self, status: int, message: str):
        if status != 404:
            log(f"refused ({status}): {message}")
        # Part of the request may be unread: no other request on this connection.
        self.close_connection = True
        self.send(status, {"error": {"message": message, "type": "invalid_request_error"}})

    def read_body(self) -> bytes:
        """The request's body, sized or sent in chunks (Open WebUI streams the recording, so: chunks)."""
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            body = bytearray()
            while True:
                size = int(self.rfile.readline().split(b";")[0].strip() or b"0", 16)
                if size == 0:
                    while self.rfile.readline() not in (b"\r\n", b"\n", b""):  # trailer lines
                        pass
                    break
                body += self.rfile.read(size)
                self.rfile.readline()  # the CRLF after each chunk
                if len(body) > MAX_UPLOAD:
                    raise ValueError("more than 100 MB of audio")
            return bytes(body)
        size = int(self.headers.get("Content-Length") or 0)
        if size > MAX_UPLOAD:
            raise ValueError("more than 100 MB of audio")
        return self.rfile.read(size)

    def do_GET(self):
        if self.path == "/health":
            self.send(200, {"status": "ok", "model": MODEL, "device": DEVICE})
        elif self.path == "/v1/models":
            self.send(200, {"object": "list", "data": [{"id": MODEL, "object": "model", "owned_by": "local"}]})
        else:
            self.error(404, "not found")

    def do_POST(self):
        if self.path.rstrip("/") != "/v1/audio/transcriptions":
            self.error(404, "not found")
            return
        try:
            form = parse_form(self.headers.get("Content-Type", ""), self.read_body())
        except ValueError as e:
            self.error(400, str(e))
            return
        audio = form.get("file")
        if not audio:
            self.error(400, "no file in the request")
            return
        language = (form.get("language") or b"").decode().strip().lower() or None
        fmt = (form.get("response_format") or b"json").decode().strip() or "json"
        fd, path = tempfile.mkstemp(prefix="whisper-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(audio)
            result = transcribe(path, language)
        except Exception as e:  # an unreadable recording
            self.error(400, f"couldn't transcribe the recording: {e}")
            return
        finally:
            os.unlink(path)
        if fmt == "text":
            self.send(200, result["text"].encode(), "text/plain; charset=utf-8")
        elif fmt == "verbose_json":
            self.send(200, result)
        else:
            self.send(200, {"text": result["text"]})


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # A client that drops its kept-alive connection (Windows' health checks do) isn't an error worth a traceback.
        if isinstance(sys.exc_info()[1], (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


def main() -> int:
    global _model, _reuse
    t0 = time.monotonic()
    log(f"loading {MODEL} on {DEVICE} ({COMPUTE_TYPE})...")
    _model = WhisperModel(MODEL, device=DEVICE, compute_type=COMPUTE_TYPE, download_root=MODEL_DIR)
    _reuse = EncoderReuse(_model)
    log(f"loaded in {time.monotonic() - t0:.1f} s; languages: {', '.join(LANGUAGES) or 'any'}; listening on "
        f"127.0.0.1:{PORT}")
    Server(("127.0.0.1", PORT), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
