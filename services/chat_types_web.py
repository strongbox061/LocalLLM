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
CHAT_TYPES_DIR = Path(os.path.expanduser(
    os.environ.get("CHAT_TYPES_DIR", "~/.config/localllm/chat-types")
))
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
    accounts. Only reads."""
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
        if key in ("name", "description", "system_prompt"):
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


def _start(kind: str, ct_id: str, args: list, after=None) -> tuple:
    """Start a background job. Returns (ok, message)."""
    cmd = BUILDER + args
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if after:
            after(proc)
        return True, f"Started {kind} for {ct_id}"
    except OSError as exc:
        return False, str(exc)


def save(body: dict) -> tuple:
    """Save a chat type file and rebuild its preset. Returns (status_code, response)."""
    ct_id = body.get("id", "")
    if not ct_id:
        return 400, {"error": "missing id"}

    data = body.get("data", {})
    cleaned = _clean(data)

    # Validate required fields
    if not cleaned.get("name"):
        return 400, {"error": "name is required"}

    # Ensure directory exists
    CHAT_TYPES_DIR.mkdir(parents=True, exist_ok=True)

    # Write the file
    target = CHAT_TYPES_DIR / f"{ct_id}.json"
    with open(target, "w") as f:
        json.dump(cleaned, f, indent=2)

    # Build the preset in background
    def _after(proc):
        subprocess.Popen(BUILDER + ["--build", ct_id])

    ok, msg = _start("save", ct_id, [str(target)], after=_after)
    if not ok:
        return 500, {"error": msg}

    return 200, {"ok": True, "id": ct_id}


def refresh(body: dict) -> tuple:
    """Refresh sources for a chat type. Returns (status_code, response)."""
    ct_id = body.get("id", "")
    if not ct_id:
        return 400, {"error": "missing id"}

    ok, msg = _start("refresh", ct_id, ["--refresh", ct_id])
    if not ok:
        return 500, {"error": msg}

    return 200, {"ok": True, "id": ct_id}
