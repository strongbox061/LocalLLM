"""
OpenAI-compatible /v1/audio/transcriptions with faster-whisper.

The model is loaded before the port opens, so the first recording is as fast as
the others, and one recording is transcribed at a time (one GPU).
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
    """Runs Whisper's encoder once per clip instead of twice.

    Detecting the language needs the encoder's output for the clip's first 30 s, and faster-whisper's transcribe()
    computes the same output again. pick_language() leaves the result here; the next encode() of an identical
    window takes it instead of running the encoder.
    """

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
    """Trim silence from the start and end using VAD."""
    sample_rate = 16000
    vad_options = VadOptions(min_silence_duration_ms=500)
    timestamps = get_speech_timestamps(audio, vad_options)
    if not timestamps:
        return audio
    chunks = collect_chunks(audio, timestamps)
    return np.concatenate(chunks) if len(chunks) > 1 else chunks[0]


def pick_language(audio):
    """Run a quick pass to detect the spoken language."""
    result = _model.transcribe(audio, beam_size=1, language=None)
    _, info = result
    return info.language if info and hasattr(info, "language") else None


def transcribe(path: str, language: str | None) -> dict:
    """Transcribe an audio file (path on disk)."""
    audio = decode_audio(path)
    audio = speech_only(audio)
    if language is None:
        language = pick_language(audio)
    return _model.transcribe(audio, beam_size=BEAM_SIZE, language=language)


def parse_form(content_type: str, body: bytes) -> dict:
    """Parse a multipart form and return field values."""
    parser = BytesParser(policy=HTTP)
    msg = parser.parsebytes(body)
    result = {}
    for part in msg.walk():
        if part.get_content_maintype() != "multipart":
            filename = part.get_filename()
            if filename:
                result["file"] = part.get_payload(decode=True)
                result["content_type"] = part.get_content_type()
            else:
                field = part.get("name")
                if field:
                    result[field] = part.get_payload(decode=True).decode("utf-8", errors="replace")
    return result


class Handler(BaseHTTPRequestHandler):
    """Handle /v1/audio/transcriptions requests."""

    def _respond(self, code: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/v1/audio/transcriptions":
            self._respond(404, {"error": "not found"})
            return

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > MAX_UPLOAD:
            self._respond(413, {"error": "payload too large"})
            return

        body = self.rfile.read(content_length)
        form = parse_form(self.headers.get("Content-Type", ""), body)

        file_data = form.get("file")
        if not file_data:
            self._respond(400, {"error": "no audio file in request"})
            return

        language = form.get("language")
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(file_data)
            tmp_path = tmp.name

        try:
            with _lock:
                result = transcribe(tmp_path, language)
            self._respond(200, result)
        except Exception as exc:
            log(f"transcription failed: {exc}")
            self._respond(500, {"error": str(exc)})
        finally:
            os.unlink(tmp_path)

    def log_message(self, fmt, *args):
        log(fmt % args)


class Server(ThreadingHTTPServer):
    """Allow socket reuse so the port can be rebound quickly."""
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    global _model
    model_path = MODEL_DIR or "medium"
    try:
        _model = EncoderReuse(model_path, DEVICE, COMPUTE_TYPE)
    except Exception as exc:
        log(f"failed to load model: {exc}")
        return 1

    server = Server(("0.0.0.0", PORT), Handler)
    log(f"listening on port {PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        log("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
