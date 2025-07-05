"""
whisper_server.py: speech-to-text for Open WebUI on the helper PC (README, "Voice in Open WebUI"), an
OpenAI-compatible /v1/audio/transcriptions with faster-whisper.

Why a service of its own: inside Open WebUI's process the same model on the same GPU took 2-3 times longer per
recording (it shares the process with the document search models), and it loaded only on the first recording after
each start (about 9 s more). Here the model is loaded before the port opens, so the first recording is as fast as
the others, and one recording is transcribed at a time (one GPU).

Language: a recording with a language given (Open WebUI sends one only when it's set in your settings) uses it;
otherwise the language is detected among LANGUAGES only, then the recording is transcribed in it. A short or
unclear clip can't come back in a language nobody here speaks.

  POST /v1/audio/transcriptions    multipart: file, optional language
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
    protocol_version = "HTTP/1.1"

    """Handle /v1/audio/transcriptions requests."""

    def send(self, status: int, body, content_type="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def error(self, status: int, message: str):
        log(f"refused ({status}): {message}")
        self.close_connection = True
        self.send(400, {"error": message})

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
        pass


class Server(ThreadingHTTPServer):
    """Allow socket reuse so the port can be rebound quickly."""
    allow_reuse_address = True
    daemon_threads = True


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
