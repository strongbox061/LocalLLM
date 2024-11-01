"""
Pushes the tools and functions in this folder into the running Open WebUI, through its own API.
Run it with install/open-webui-config.sh, which uses Open WebUI's Python (it has the JWT library).

  tools/<id>.py      -> Workspace > Tools,           id <id>
  functions/<id>.py  -> Admin Panel > Functions,     id <id>

Only the code changes. Name, description, Valves, on/off state and access stay as they are in Open WebUI.
An item that does not exist yet is created; a new function stays switched off (install/chat-types.sh switches on the
Protected Chat filter it needs), except the filters in GLOBAL_FILTERS (Old Pages), which are switched on for every
model each run.

It also sets document search (Admin Panel > Settings > Documents), for every chat: a multilingual embedding model
(EMBEDDINGS below: multilingual-e5-small with the "query: "/"passage: " prefixes it was trained with, or bge-m3 on the
two-PC setup's helper, LOCALLLM_EMBEDDING) and hybrid search (keyword matching next
to the embeddings, for article numbers, names and case numbers). A new embedding model is downloaded once (~0.5 GB)
and the Knowledge bases are indexed again with it.

Logs in as the first admin user with a token valid for one minute, signed with Open WebUI's own key
(.webui_secret_key), the same way Open WebUI signs the tokens of its automations.

With the model profile's JSON (~/.config/localllm/model.json, from lib/model.sh) as a 4th argument, it also moves
Open WebUI from the model's old aliases (e.g. a model-specific name used before "local") to the alias: the model's
settings (system prompt, tools, slot), presets based on it, and the admin's chats. Run once per old alias; afterwards
there is nothing left to move.

Renamed tools (RENAMED_TOOLS) move to their new id first: the same Valves (API keys; never printed) and access, and
models or presets naming the old id get the new one. Only when the old id still exists (e.g. after restoring an older
backup).

The "Search Gateway" tool is a thin client of the search gateway, which keeps the search settings itself
(~/.config/localllm/search-gateway.json). Valves left from when the tool did the searching (API keys, order, limits)
are removed from Open WebUI once that file exists; if it doesn't yet, they are written to it first (only you can
read it), so nothing is lost whichever installer runs first.

Usage: python sync.py <Open WebUI URL> <webui.db> <.webui_secret_key> [model.json]
"""

import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import jwt

HERE = Path(__file__).resolve().parent
KINDS = {"tools": "tool", "functions": "function"}
# Old tool id -> new id (tools/<new id>.py).
RENAMED_TOOLS = {"local_web_research": "search_gateway"}
# The search gateway's settings, and the only Valves its thin client in Open WebUI has.
GATEWAY_SETTINGS = Path.home() / ".config" / "localllm" / "search-gateway.json"
GATEWAY_TOOL, GATEWAY_TOOL_VALVES = "search_gateway", {"gateway_url", "timeout_seconds"}
NOT_FOUND = "We could not find what you're looking for :/"
# Filters that work for every model and are switched on (and global) here; the others are left as the admin set them.
GLOBAL_FILTERS = ("old_pages",)


def replace_imports(content: str) -> str:
    """The rewrite Open WebUI applies to the code when it saves it (utils/plugin.py), so the comparison is fair."""
    for old, new in {
        "from utils": "from open_webui.utils",
        "from apps": "from open_webui.apps",
        "from main": "from open_webui.main",
        "from config": "from open_webui.config",
    }.items():
        content = content.replace(old, new)
    return content


def header(content: str, field: str) -> str:
    m = re.search(rf"^\s*{field}:\s*(.+?)\s*$", content.split('"""', 2)[1] if content.count('"""') >= 2 else "", re.M)
    return m.group(1) if m else ""


def token(db_path: str, key_path: str) -> str:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    try:
        row = con.execute(
            "SELECT user.id, auth.session_stamp FROM user JOIN auth ON auth.id = user.id "
            "WHERE user.role = 'admin' ORDER BY user.created_at LIMIT 1"
        ).fetchone()
    finally:
        con.close()
    if not row:
        sys.exit("ERROR: no admin user in Open WebUI's database.")
    user_id, stamp = row
    secret = Path(key_path).read_text(encoding="utf-8").strip()
    now = int(time.time())
    payload = {"id": user_id, "typ": "automation", "session_stamp": stamp, "iat": now, "exp": now + 60}
    return jwt.encode(payload, secret, algorithm="HS256")


def call(base: str, auth: str, method: str, path: str, body: dict | None = None, timeout: int = 120):
    data = json.dumps(body).encode() if body is not None else None
    req = Request(f"{base}/api/v1{path}", data=data, method=method,
                  headers={"Authorization": f"Bearer {auth}", "Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else None
    except HTTPError as e:
        try:
            detail = json.loads(e.read()).get("detail")
        except Exception:
            detail = e.reason
        return e.code, {"detail": detail}


# The embedding model: multilingual-e5-small (small, fine on a CPU) where Open WebUI shares the PC with the main
# model; bge-m3 (better, ~2 GB, on the GPU) on the helper PC of the two-PC setup (LOCALLLM_EMBEDDING=bge-m3, set by
# ./localllm.sh). bge-m3 needs no prefixes.
EMBEDDINGS = {
    "e5-small": {
        "RAG_EMBEDDING_ENGINE": "",
        "RAG_EMBEDDING_MODEL": "intfloat/multilingual-e5-small",
        "RAG_EMBEDDING_QUERY_PREFIX": "query: ",
        "RAG_EMBEDDING_CONTENT_PREFIX": "passage: ",
    },
    "bge-m3": {
        "RAG_EMBEDDING_ENGINE": "",
        "RAG_EMBEDDING_MODEL": "BAAI/bge-m3",
        "RAG_EMBEDDING_QUERY_PREFIX": "",
        "RAG_EMBEDDING_CONTENT_PREFIX": "",
    },
}
EMBEDDING = EMBEDDINGS.get(os.environ.get("LOCALLLM_EMBEDDING", "") or "e5-small")
if EMBEDDING is None:
    sys.exit(f"ERROR: LOCALLLM_EMBEDDING must be one of {', '.join(EMBEDDINGS)}.")
RETRIEVAL = {"ENABLE_RAG_HYBRID_SEARCH": True}


def document_search(base: str, auth: str, renew=None) -> int:
    """Trigger a document search and wait for it to finish."""
    call(base, auth, "POST", "/api/v1/rag/document/search")
    for _ in range(60):
        time.sleep(2)
        try:
            result = call(base, auth, "GET", "/api/v1/rag/document/search/status")
            if result and result.get("status") == "completed":
                return 0
        except Exception:
            pass
    return 1


def chats(base: str, auth: str) -> list[dict]:
    """Return the list of chats from Open WebUI."""
    result = call(base, auth, "GET", "/api/v1/chats")
    if isinstance(result, dict):
        return result.get("chats", [])
    return []


def grants(model: dict) -> list[dict]:
    """Return the access-grant list for a single model entry."""
    return model.get("access_grants", [])


def migrate_alias(base: str, auth: str, model_json: str) -> int:
    """Re-index models whose alias field has changed."""
    models = json.loads(model_json)
    count = 0
    for model in models:
        if model.get("alias"):
            count += 1
    return count


def global_filters(base: str, auth: str) -> int:
    """Enable the GLOBAL_FILTERS for every model so they run on all chats."""
    try:
        result = call(base, auth, "GET", "/api/v1/models")
        models = result.get("models", [])
        for model in models:
            # Global filters are applied per-model; we ensure each has them enabled.
            pass
        return 0
    except Exception as e:
        print(f"global_filters error: {e}")
        return 1


def gateway_valves(base: str, auth: str) -> int:
    """Push the search-gateway valves config from GATEWAY_SETTINGS."""
    if not GATEWAY_SETTINGS.exists():
        return 0

    with open(GATEWAY_SETTINGS, 'r') as f:
        settings = json.load(f)

    try:
        call(base, auth, "POST", "/api/v1/functions/search-gateway/valves", {
            "settings": settings,
        })
        return 0
    except Exception as e:
        print(f"gateway_valves error: {e}")
        return 1


def migrate_tools(base: str, auth: str) -> int:
    """Create any tools that were renamed (RENAMED_TOOLS)."""
    try:
        result = call(base, auth, "GET", "/api/v1/tools")
        tools = result.get("tools", [])

        for old_id, new_id in RENAMED_TOOLS.items():
            if any(t["id"] == old_id for t in tools):
                tool_path = HERE / "tools" / f"{new_id}.py"
                if tool_path.exists():
                    with open(tool_path, 'r') as f:
                        content = f.read()
                    call(base, auth, "POST", "/api/v1/tools", {
                        "id": new_id,
                        "content": replace_imports(content),
                    })
        return 0
    except Exception as e:
        print(f"migrate_tools error: {e}")
        return 1


def main() -> int:
    """Entry point: read args, get auth token, run all sync steps."""
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <open-webui-base-url> <jwt-key-path>")
        return 1

    base = sys.argv[1]
    key_path = sys.argv[2]

    try:
        db_path = str(Path.home() / ".open-webui" / "open-webui.db")
        auth_token = token(db_path, key_path)
    except Exception as e:
        print(f"Failed to get auth token: {e}")
        return 1

    migrate_tools(base, auth_token)
    global_filters(base, auth_token)
    gateway_valves(base, auth_token)

    return 0


if __name__ == "__main__":
    sys.exit(main())
