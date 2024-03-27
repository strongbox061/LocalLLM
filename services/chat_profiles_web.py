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
FORM_KEYS = (
    "name", "description", "system_prompt", "allowed_sites",
    "sources", "patterns", "allowed_patterns",
)
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}

# Where overview() reads Open WebUI's status: None = the database at db_path on this computer; or a function
# returning db_status()'s answer from elsewhere (chat_types_server.py: the helper PC's, over SSH).
STATUS_SOURCE = None
ON_MAIN = os.environ.get("CHAT_TYPES_ON_MAIN") == "1"


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
    """Return a fresh chat-type template from example.json or defaults."""
    if EXAMPLE.exists():
        with open(EXAMPLE) as f:
            return json.load(f)
    return {
        "name": "",
        "description": "",
        "system_prompt": "",
        "allowed_sites": [],
        "sources": [],
        "patterns": [],
        "allowed_patterns": [],
    }


MARKER_RE = re.compile(r"\[chat type: ([a-z0-9-]+)\]")


def db_status(db_path: str) -> dict:
    """Query Open WebUI's database and return a status dict per chat type."""
    rows = _db_rows(
        db_path,
        "SELECT id, name, metadata_ FROM chats WHERE metadata_ IS NOT NULL",
    )
    result = {}
    for row_id, name, meta_raw in rows:
        try:
            meta = json.loads(meta_raw) if meta_raw else {}
        except (json.JSONDecodeError, TypeError):
            continue
        # Look for our marker in the metadata
        chat_type_id = None
        for k, v in meta.items():
            m = MARKER_RE.search(str(v))
            if m:
                chat_type_id = m.group(1)
                break
        if chat_type_id:
            result[chat_type_id] = {
                "id": row_id,
                "name": name or "",
                "active": True,
            }
    return result


def overview(db_path: str) -> dict:
    """Build a full overview: chat types, their status, accounts, template, and job."""
    # Gather existing chat-type files
    types = {}
    if CHAT_TYPES_DIR.exists():
        for f in sorted(CHAT_TYPES_DIR.glob("*.json")):
            if f.name.startswith("."):
                continue
            try:
                with open(f) as fh:
                    data = json.load(fh)
                types[f.stem] = data
            except (json.JSONDecodeError, OSError):
                pass

    # Get Open WebUI status
    if STATUS_SOURCE is not None:
        if callable(STATUS_SOURCE):
            status = STATUS_SOURCE()
        else:
            status = db_status(db_path)
    else:
        status = db_status(db_path)

    return {
        "types": types,
        "status": status,
        "template": _template(),
        "job": job(),
    }


def job() -> dict:
    """Return the current background job state (running command or None)."""
    # Simplified v1: just return idle
    return {"running": False, "command": None}


# ---------------------------------------------------------------- changing

def _clean(data: dict) -> dict:
    """Only the form's keys, with list items trimmed and empty ones dropped."""
    out = {}
    for key in FORM_KEYS:
        value = data.get(key)
        if key in ("name", "description", "system_prompt"):
            out[key] = str(value or "").strip()
        elif key == "sources":
            out[key] = [{"name": str(s.get("name", "")).strip(),
                         "urls": [str(u).strip() for u in s.get("urls") or [] if str(u).strip()]}
                        for s in value or [] if isinstance(s, dict) and (s.get("name") or s.get("urls"))]
        elif key in ("patterns", "allowed_patterns"):
            out[key] = [{"name": str(p.get("name", "")).strip(), "regex": str(p.get("regex", ""))}
                        for p in value or [] if isinstance(p, dict) and (p.get("name") or p.get("regex"))]
        else:
            out[key] = [str(v).strip() for v in value or [] if str(v).strip()]
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
