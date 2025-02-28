"""
chat_types_web.py: the "Chat types" page of the search gateway (http://127.0.0.1:8090/chat-types), a form for the
protected chats' chat type files (README, "Protected chats in Open WebUI").

The page edits the same files as by hand (~/.config/localllm/chat-types/<id>.json) and runs the same builder
(install/chat-types.sh) in the background, so every protection setting is still set by code, never by the form.

  GET  /chat-types               the page (services/chat_types_page.html)
  GET  /chat-types/data          the chat types, their status in Open WebUI, the accounts, the template, the job
  POST /chat-types/save          {"id", "data", "new"}: check, write the file, then build its preset
  POST /chat-types/refresh       {"id"}: download its sources into its Knowledge base
  POST /chat-types/delete        {"id"}: delete its preset and Knowledge base, and move the file to .deleted/
  GET  /chat-types/job           the running or last job and its output

Only from this computer: the client must be on 127.0.0.1/::1 and the Host header 127.0.0.1 or localhost (against DNS
rebinding); a change must be a JSON POST whose Origin, when sent, is this page's (against other websites posting to
it). One job at a time.

Split mode (README, "Two-PC setup"): the chat type files stay on the main PC, Open WebUI runs on the helper PC.
  - On the main PC, `./localllm.sh chat-types` serves this page (services/chat_types_server.py), with the status of
    the presets, Knowledge bases and accounts read on the helper (STATUS_SOURCE) and install/chat-types.sh building
    there.
  - On the helper, the search gateway's copy of the page only points to the main PC (CHAT_TYPES_ON_MAIN=1, set by
    windows/common.ps1): a file saved there would be overwritten by the next deploy.
"""

import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHAT_TYPES_DIR = Path(os.path.expanduser(os.environ.get("CHAT_TYPES_DIR", "~/.config/localllm/chat-types")))
EXAMPLE = REPO / "open-webui" / "chat-types" / "example.json"
PAGE = Path(__file__).resolve().parent / "chat_types_page.html"
CSS_FILE = Path(__file__).resolve().parent / "chat_types_page.css"
JS_FILE = Path(__file__).resolve().parent / "chat_types_page.js"
BUILDER = [str(REPO / "install" / "chat-types.sh")]
FORM_KEYS = ("name", "description", "system_prompt", "allowed_sites", "sources", "patterns", "allowed_patterns",
             "allowed_terms", "reference_words", "starter_prompts", "users")
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}

_spec = importlib.util.spec_from_file_location("chat_types_builder", REPO / "open-webui" / "chat_types.py")
builder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(builder)

# Where overview() reads Open WebUI's status: None = the database at db_path on this computer; or a function
# returning db_status()'s answer from elsewhere (chat_types_server.py: the helper PC's, over SSH).
STATUS_SOURCE = None
ON_MAIN = os.environ.get("CHAT_TYPES_ON_MAIN") == "1"

_job_lock = threading.Lock()
_job = {"running": False, "kind": "", "id": "", "started": 0, "ended": 0, "exit": None, "output": ""}


# ---------------------------------------------------------------- reading

def _db_rows(db_path: str, sql: str, args=()) -> list:
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
        try:
            return con.execute(sql, args).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return []


def _template() -> dict:
    try:
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {k: v for k, v in data.items() if not k.startswith("_")}


MARKER_RE = re.compile(r"\[chat type: ([a-z0-9-]+)\]")


def db_status(db_path: str) -> dict:
    """What Open WebUI has of the chat types: their presets' last update, their Knowledge bases (name, files), the
    accounts. Only reads. Also run on the helper PC (windows/tasks.ps1 chat-types-status)."""
    presets = {mid[len("chat-"):]: updated for mid, updated in
               _db_rows(db_path, "SELECT id, updated_at FROM model WHERE id LIKE 'chat-%'")}
    knowledge = {}
    for name, description, files in _db_rows(
            db_path, "SELECT k.name, k.description, COUNT(kf.file_id) FROM knowledge k LEFT JOIN knowledge_file kf "
                     "ON kf.knowledge_id = k.id WHERE k.description LIKE '%[chat type: %' GROUP BY k.id"):
        m = MARKER_RE.search(description or "")
        if m and m.group(1) not in knowledge:
            knowledge[m.group(1)] = {"name": name, "files": files}
    users = [{"email": e, "name": n, "role": r} for e, n, r in
             _db_rows(db_path, "SELECT email, name, role FROM user ORDER BY created_at")]
    return {"presets": presets, "knowledge": knowledge, "users": users}


def overview(db_path: str) -> dict:
    error = ""
    try:
        status = STATUS_SOURCE() if STATUS_SOURCE else db_status(db_path)
    except Exception as e:  # the helper unreachable: the files still show, without Open WebUI's side
        status, error = {"presets": {}, "knowledge": {}, "users": []}, f"{type(e).__name__}: {e}"
    types = []
    for f in sorted(CHAT_TYPES_DIR.glob("*.json")) if CHAT_TYPES_DIR.is_dir() else []:
        ct, problems = builder.load_chat_type(f)
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        data = {k: v for k, v in raw.items() if not k.startswith("_")} if isinstance(raw, dict) else {}
        updated = status["presets"].get(f.stem)
        types.append({
            "id": f.stem, "data": data, "problems": problems,
            "file_changed": int(f.stat().st_mtime),
            "preset": {"exists": f.stem in status["presets"], "updated": updated},
            "knowledge": status["knowledge"].get(f.stem),
        })
    return {"folder": str(CHAT_TYPES_DIR), "types": types, "users": status["users"], "template": _template(),
            "job": job(), "status_error": error}


def job() -> dict:
    with _job_lock:
        return dict(_job)


# ---------------------------------------------------------------- changing

def _clean(data: dict) -> dict:
    """Only the form's keys, in the template's order, with list items trimmed and empty ones dropped."""
    out = {}
    for key in FORM_KEYS:
        value = data.get(key)
        if key == "name":
            name = str(value or "").strip()
            out[key] = name[:80] if len(name) > 80 else name
        elif key in ("description", "system_prompt"):
            out[key] = str(value or "").strip()
        elif key in ("allowed_sites", "allowed_terms", "reference_words", "starter_prompts", "users"):
            out[key] = [str(v).strip() for v in value or [] if str(v).strip()]
        elif key == "sources":
            out[key] = [{"name": str(s.get("name", "")).strip(),
                         "urls": [str(u).strip() for u in s.get("urls") or [] if str(u).strip()]}
                        for s in value or [] if isinstance(s, dict) and (s.get("name") or s.get("urls"))]
        else:  # patterns, allowed_patterns
            out[key] = [{"name": str(p.get("name", "")).strip(), "regex": str(p.get("regex", ""))}
                        for p in value or [] if isinstance(p, dict) and (p.get("name") or p.get("regex"))]
    out["allowed_sites"] = [re.sub(r"^https?://", "", s).split("/")[0].lower() for s in out["allowed_sites"]]
    return out


def _start(kind: str, ct_id: str, args: list, after=None) -> tuple[bool, str]:
    with _job_lock:
        if _job["running"]:
            return False, f"Busy: {_job['kind']} of {_job['id']} is still running."
        _job.update(running=True, kind=kind, id=ct_id, started=int(time.time()), ended=0, exit=None, output="")

    def run():
        try:
            p = subprocess.Popen(BUILDER + args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 cwd=str(REPO), env={**os.environ, "PYTHONUNBUFFERED": "1"})
            for line in p.stdout:
                with _job_lock:
                    _job["output"] += line
            code = p.wait()
            if code == 0 and after:
                after()
        except Exception as e:
            code = -1
            with _job_lock:
                _job["output"] += f"ERROR: {type(e).__name__}: {e}\n"
        with _job_lock:
            _job.update(running=False, exit=code, ended=int(time.time()))

    threading.Thread(target=run, daemon=True).start()
    return True, "started"


def save(body: dict) -> tuple[int, dict]:
    ct_id = str(body.get("id") or "").strip()
    if not builder.ID_RE.match(ct_id):
        return 400, {"problems": ["The id must be lower case letters, digits and dashes (up to 41), e.g. research-notes."]}
    path = CHAT_TYPES_DIR / f"{ct_id}.json"
    if body.get("new") and path.exists():
        return 400, {"problems": [f"A chat type {ct_id} exists already: pick another id."]}
    if not body.get("new") and not path.exists():
        return 400, {"problems": [f"No chat type {ct_id} any more (deleted meanwhile?)."]}
    data = _clean(body.get("data") or {})
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / f"{ct_id}.json"
        probe.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        _, problems = builder.load_chat_type(probe)
    if problems:
        return 400, {"problems": problems}
    if job()["running"]:
        return 409, {"problems": ["Another job is running: wait until it ends, then save again."]}
    # Notes (keys starting with _) written by hand stay.
    try:
        old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        old = {}
    notes = {k: v for k, v in old.items() if k.startswith("_")} if isinstance(old, dict) else {}
    CHAT_TYPES_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({**notes, **data}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    started, why = _start("save", ct_id, ["--only", ct_id])
    return 200, {"saved": True, "job": started, "message": why}


def refresh(body: dict) -> tuple[int, dict]:
    ct_id = str(body.get("id") or "")
    if not builder.ID_RE.match(ct_id) or not (CHAT_TYPES_DIR / f"{ct_id}.json").is_file():
        return 400, {"problems": ["No such chat type."]}
    started, why = _start("download sources", ct_id, ["--refresh-sources", "--only", ct_id])
    return (200 if started else 409), {"job": started, "message": why}


def delete(body: dict) -> tuple[int, dict]:
    ct_id = str(body.get("id") or "")
    path = CHAT_TYPES_DIR / f"{ct_id}.json"
    if not builder.ID_RE.match(ct_id) or not path.is_file():
        return 400, {"problems": ["No such chat type."]}

    def move_file():  # kept, not erased: the settings backup saves .deleted/ too
        trash = CHAT_TYPES_DIR / ".deleted"
        trash.mkdir(exist_ok=True)
        os.replace(path, trash / f"{ct_id}-{time.strftime('%Y%m%d-%H%M%S')}.json")

    started, why = _start("delete", ct_id, ["--remove", ct_id], after=move_file)
    return (200 if started else 409), {"job": started, "message": why}


# ---------------------------------------------------------------- HTTP

ON_MAIN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Chat types</title>
<style>body{font:15px/1.5 system-ui,sans-serif;max-width:640px;margin:40px auto;padding:0 16px;color:#222;background:#fff}
code{background:#f1f1f1;padding:1px 5px;border-radius:4px}@media(prefers-color-scheme:dark){body{color:#ddd;background:#1b1b1b}
code{background:#333}}</style></head><body>
<h1>Chat types are edited on the main PC</h1>
<p>Open WebUI runs on this helper PC, but the chat type files live on the main PC, so they are edited there. On the
main PC, run</p>
<p><code>./localllm.sh chat-types</code></p>
<p>and open the address it prints (<code>http://127.0.0.1:8090/chat-types</code>). Saving there builds the preset
here. A file changed on this PC would be overwritten by the next deploy.</p>
<p><a href="/dashboard">Search usage dashboard</a></p>
</body></html>
"""


def _local(handler, port: int) -> bool:
    client = handler.client_address[0] if handler.client_address else ""
    host = (handler.headers.get("Host") or "").strip().lower()
    host_name = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
    return client in ("127.0.0.1", "::1", "::ffff:127.0.0.1") and host_name in LOCAL_HOSTS


def _same_origin(handler) -> bool:
    origin = (handler.headers.get("Origin") or "").strip().lower()
    host = (handler.headers.get("Host") or "").strip().lower()
    return not origin or origin == f"http://{host}"


def handle(handler, path: str, method: str, body: bytes, db_path: str, port: int) -> bool:
    """Answers the /chat-types paths; returns False for any other path."""
    if path != "/chat-types" and not path.startswith("/chat-types/"):
        return False
    if not _local(handler, port):
        handler._send(403, {"error": "The chat types page is only served to this computer (http://127.0.0.1)."})
        return True
    if ON_MAIN:
        if method == "GET" and path == "/chat-types":
            handler._send_bytes(200, ON_MAIN_PAGE.encode("utf-8"), "text/html; charset=utf-8")
        else:
            handler._send(409, {"error": "In split mode the chat types are edited on the main PC: ./localllm.sh chat-types"})
        return True
    if method == "GET":
        assets = {
            "/chat-types": (PAGE, "text/html; charset=utf-8"),
            "/chat-types/chat_types_page.css": (CSS_FILE, "text/css; charset=utf-8"),
            "/chat-types/chat_types_page.js": (JS_FILE, "application/javascript; charset=utf-8"),
        }
        if path in assets:
            file_path, content_type = assets[path]
            try:
                handler._send_bytes(200, file_path.read_bytes(), content_type)
            except OSError as e:
                handler._send(500, {"error": f"page missing: {e}"})
        elif path == "/chat-types/data":
            handler._send(200, overview(db_path))
        elif path == "/chat-types/job":
            handler._send(200, job())
        else:
            handler._send(404, {"error": "unknown page"})
        return True
    actions = {"/chat-types/save": save, "/chat-types/refresh": refresh, "/chat-types/delete": delete}
    if path not in actions:
        handler._send(404, {"error": "unknown action"})
        return True
    if "application/json" not in (handler.headers.get("Content-Type") or "") or not _same_origin(handler):
        handler._send(403, {"error": "Changes are only accepted from the chat types page itself."})
        return True
    try:
        data = json.loads(body.decode("utf-8") or "{}")
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except ValueError as e:
        handler._send(400, {"problems": [f"Bad request: {e}"]})
        return True
    status, result = actions[path](data)
    handler._send(status, result)
    return True
