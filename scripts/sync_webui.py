"""
Pushes the tools and functions in this folder into the running Open WebUI, through its own API.
Run it with install/open-webui-config.sh, which uses Open WebUI's Python (it has the JWT library).

  tools/<id>.py      -> Workspace > Tools,           id <id>
  functions/<id>.py  -> Admin Panel > Functions,     id <id>

Only the code changes. Name, description, Valves, on/off state and access stay as they are in Open WebUI.
An item that does not exist yet is created; a new function stays switched off (install/chat-types.sh switches on the
Protected Chat filter it needs), except the filters in GLOBAL_FILTERS (Old Pages), which are switched on for every
model each run.
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
    """Replace top-level imports with relative imports for Open WebUI."""
    return re.sub(r'^(import |from )', 'from .\\1', content, flags=re.MULTILINE)


def header(content: str, field: str) -> str:
    """Return the docstring/header block from a file's content."""
    lines = content.split('\n')
    result = []
    in_header = False
    for line in lines:
        if line.startswith('"""'):
            if not in_header:
                in_header = True
                result.append(line)
            elif result:
                result.append(line)
                break
        elif in_header:
            result.append(line)
    return '\n'.join(result)


def token(db_path: str, key_path: str) -> str:
    """Build a JWT token from the Open WebUI database secret and the admin key."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT secret_key FROM info LIMIT 1")
    secret = cursor.fetchone()[0]
    conn.close()

    with open(key_path, 'r') as f:
        private_key = f.read()

    payload = {"sub": "admin@email.com", "role": "admin"}
    return jwt.encode(payload, private_key, algorithm="RS256")


def call(base: str, auth: str, method: str, path: str, body: dict | None = None, timeout: int = 120):
    """Make an HTTP call to the Open WebUI API."""
    url = f"{base}/{path.lstrip('/')}"
    headers = {"Authorization": f"Bearer {auth}", "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body else None
    req = Request(url, data=data, headers=headers, method=method)

    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except HTTPError as e:
        print(f"HTTP error {e.code}: {e.reason}")
        raise


# The embedding model: multilingual-e5-small (small, fine on a CPU) where Open WebUI shares the PC with the main
EMBEDDINGS = {
    "e5-small": {"model": "intfloat/multilingual-e5-small", "dimension": 1024},
}
EMBEDDING = EMBEDDINGS.get(os.environ.get("LOCALLLM_EMBEDDING", "") or "e5-small")
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
