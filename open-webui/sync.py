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
    """Sets the embedding model and hybrid search when they differ. Returns the number of failures. renew() gives a
    new login token: the model change and the re-indexing can take many minutes, longer than a token lasts."""
    renew = renew or (lambda: auth)
    failed = 0
    status, current = call(base, auth, "GET", "/retrieval/embedding")
    if status != 200:
        print(f"  FAILED   document search: can't read the embedding settings ({status})")
        return 1
    if any((current.get(k) or "") != v for k, v in EMBEDDING.items()):
        body = {**{k: current.get(k) for k in ("RAG_EMBEDDING_BATCH_SIZE", "ENABLE_ASYNC_EMBEDDING",
                                               "RAG_EMBEDDING_CONCURRENT_REQUESTS")}, **EMBEDDING}
        print(f"  ...      embedding model {current.get('RAG_EMBEDDING_MODEL')} -> {EMBEDDING['RAG_EMBEDDING_MODEL']} "
              "(downloaded the first time, ~0.5 GB)")
        status, result = call(base, auth, "POST", "/retrieval/embedding/update", body, timeout=1800)
        if status == 200:
            print(f"  updated  embedding model {EMBEDDING['RAG_EMBEDDING_MODEL']}")
            auth = renew()
            status, kbs = call(base, auth, "GET", "/knowledge/")
            if status == 200 and (kbs or {}).get("items"):
                status, result = call(base, auth, "POST", "/knowledge/reindex", None, timeout=3600)
                print(f"  {'updated ' if status == 200 else 'FAILED  '} Knowledge bases indexed again with it")
                failed += status != 200
                auth = renew()
                # The Knowledge bases' own names and descriptions (what "search knowledge bases" looks in) are a
                # separate collection, which /knowledge/reindex leaves with the old model's vectors.
                status, result = call(base, auth, "POST", "/knowledge/metadata/reindex", None, timeout=600)
                print(f"  {'updated ' if status == 200 else 'FAILED  '} Knowledge bases' descriptions indexed again")
                failed += status != 200
        else:
            failed += 1
            print(f"  FAILED   embedding model: {status} {(result or {}).get('detail', '')}")
    else:
        print(f"  same     embedding model {EMBEDDING['RAG_EMBEDDING_MODEL']}")
    status, current = call(base, auth, "GET", "/retrieval/config")
    if status == 200 and all(current.get(k) == v for k, v in RETRIEVAL.items()):
        print("  same     hybrid search on")
    else:
        status, result = call(base, auth, "POST", "/retrieval/config/update", RETRIEVAL)
        ok = status == 200 and all((result or {}).get(k) == v for k, v in RETRIEVAL.items())
        print(f"  {'updated ' if ok else 'FAILED  '} hybrid search on" + ("" if ok else f": {status}"))
        failed += not ok
    return failed


def chats(base: str, auth: str) -> list[dict]:
    """All of the token's user's chats (archived too), from the NDJSON export."""
    req = Request(f"{base}/api/v1/chats/all", headers={"Authorization": f"Bearer {auth}"})
    with urlopen(req, timeout=300) as r:
        return [json.loads(line) for line in r.read().decode().splitlines() if line.strip()]


def grants(model: dict) -> list[dict]:
    keys = ("principal_type", "principal_id", "permission")
    return [{k: g[k] for k in keys} for g in model.get("access_grants") or []]


def migrate_alias(base: str, auth: str, model_json: str) -> int:
    """Moves Open WebUI from the profile's old aliases to its alias. Returns the number of failures."""
    try:
        profile = json.loads(Path(model_json).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"  skipped  model alias: can't read {model_json} ({e}); run install/llama.sh first")
        return 0
    alias, old = profile.get("alias") or "local", [a for a in profile.get("oldAliases") or [] if a]
    failed = 0
    if not old:
        return 0

    def report(status, result, what):
        nonlocal failed
        if status == 200:
            print(f"  moved    {what}")
        else:
            failed += 1
            print(f"  FAILED   {what}: {status} {(result or {}).get('detail', '')}")

    # The model's own settings: a model entry with the served id, which overrides that model's defaults.
    for old_id in old:
        status, current = call(base, auth, "GET", f"/models/model?id={old_id}")
        if status != 200 or not current or current.get("id") != old_id:
            continue
        status, existing = call(base, auth, "GET", f"/models/model?id={alias}")
        if status == 200 and existing and existing.get("id") == alias:
            print(f"  kept     model {alias} (exists already); {old_id}'s settings left as they are")
            continue
        body = {"id": alias, "base_model_id": None,
                "name": alias if current.get("name") == old_id else current.get("name") or alias,
                "meta": current.get("meta") or {}, "params": current.get("params") or {},
                "access_grants": grants(current), "is_active": current.get("is_active", True),
                "commit_message": f"Moved from {old_id} by the LocalLLM repo"}
        status, result = call(base, auth, "POST", "/models/create", body)
        report(status, result, f"model settings {old_id} -> {alias}")
        if status == 200:
            status, result = call(base, auth, "POST", "/models/model/delete", {"id": old_id})
            if status != 200:
                failed += 1
                print(f"  FAILED   removing model {old_id}: {status} {(result or {}).get('detail', '')}")

    # Presets (workspace models) built on the old id.
    status, models = call(base, auth, "GET", "/models/export")
    for m in models if status == 200 and isinstance(models, list) else []:
        if m.get("base_model_id") in old:
            body = {"id": m["id"], "base_model_id": alias, "name": m["name"], "meta": m.get("meta") or {},
                    "params": m.get("params") or {}, "access_grants": grants(m), "is_active": m.get("is_active", True),
                    "commit_message": f"Base model {m['base_model_id']} -> {alias} (LocalLLM repo)"}
            status, result = call(base, auth, "POST", "/models/model/update", body)
            report(status, result, f"preset {m['id']}: base model {m['base_model_id']} -> {alias}")

    # Chats: the model a chat continues with. Only the admin's own chats can be changed through the API; the others
    # get the default model when they are opened again.
    moved = 0
    for chat in chats(base, auth):
        models_ = (chat.get("chat") or {}).get("models") or []
        if any(m in old for m in models_):
            new = [alias if m in old else m for m in models_]
            status, result = call(base, auth, "POST", f"/chats/{chat['id']}", {"chat": {"models": new}})
            if status == 200:
                moved += 1
            else:
                failed += 1
                print(f"  FAILED   chat {chat['id']}: {status} {(result or {}).get('detail', '')}")
    if moved:
        print(f"  moved    {moved} chat(s) -> {alias}")
    return failed


def global_filters(base: str, auth: str) -> int:
    """Switches GLOBAL_FILTERS on, for every model. Returns the number of failures."""
    failed = 0
    for fid in GLOBAL_FILTERS:
        status, fn = call(base, auth, "GET", f"/functions/id/{fid}")
        if status != 200 or not fn:
            continue  # reported by the sync loop
        for flag, path, what in (("is_active", "toggle", "on"), ("is_global", "toggle/global", "global (every model)")):
            if not fn.get(flag):
                status, result = call(base, auth, "POST", f"/functions/id/{fid}/{path}")
                print(f"  {'switched' if status == 200 else 'FAILED  '} filter {fid} {what}")
                failed += status != 200
                fn = result if status == 200 and isinstance(result, dict) else fn
    return failed


def gateway_valves(base: str, auth: str) -> int:
    """Moves the search settings out of Open WebUI (see the docstring). Returns the number of failures."""
    status, valves = call(base, auth, "GET", f"/tools/id/{GATEWAY_TOOL}/valves")
    if status != 200 or not isinstance(valves, dict):
        return 0
    old = {k: v for k, v in valves.items() if k not in GATEWAY_TOOL_VALVES and v not in (None, "")}
    if not old:
        return 0
    if not GATEWAY_SETTINGS.exists():
        GATEWAY_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        tmp = GATEWAY_SETTINGS.with_name(".search-gateway.json.tmp")
        tmp.write_text(json.dumps({k: v for k, v in old.items() if k not in ("usage_file", "cache_file")},
                                  indent=2, sort_keys=True) + "\n")
        tmp.chmod(0o600)
        tmp.replace(GATEWAY_SETTINGS)
        print(f"  moved    the search settings from Open WebUI to {GATEWAY_SETTINGS} ({len(old)} values)")
    keep = {k: v for k, v in valves.items() if k in GATEWAY_TOOL_VALVES}
    status, _ = call(base, auth, "POST", f"/tools/id/{GATEWAY_TOOL}/valves/update", keep)
    if status != 200:
        print(f"  FAILED   removing the old search settings from Open WebUI's tool {GATEWAY_TOOL}: {status}")
        return 1
    print(f"  removed  the old search settings from Open WebUI's tool {GATEWAY_TOOL} (the gateway has them)")
    return 0


def migrate_tools(base: str, auth: str) -> int:
    """Moves each renamed tool (RENAMED_TOOLS) to its new id. Returns the number of failures."""
    failed = 0
    for old_id, new_id in RENAMED_TOOLS.items():
        status, old = call(base, auth, "GET", f"/tools/id/{old_id}")
        if status != 200 or not old or old.get("id") != old_id:
            continue
        status, existing = call(base, auth, "GET", f"/tools/id/{new_id}")
        if status == 200 and existing and existing.get("id") == new_id:
            print(f"  kept     tool {new_id} (exists already); tool {old_id} left as it is")
            continue
        content = (HERE / "tools" / f"{new_id}.py").read_text(encoding="utf-8")
        body = {"id": new_id, "name": header(content, "title") or new_id, "content": content,
                "meta": old.get("meta") or {}, "access_grants": grants(old),
                "commit_message": f"Renamed from {old_id} by the LocalLLM repo"}
        status, result = call(base, auth, "POST", "/tools/create", body)
        if status != 200:
            failed += 1
            print(f"  FAILED   tool {old_id} -> {new_id}: {status} {(result or {}).get('detail', '')}")
            continue
        # The Valves (API keys) as they are, with the old data files' paths renamed. Never printed.
        status, valves = call(base, auth, "GET", f"/tools/id/{old_id}/valves")
        if status == 200 and isinstance(valves, dict):
            for key, value in valves.items():
                if isinstance(value, str):
                    for old_name, new_name in RENAMED_FILES.items():
                        if value.endswith("/" + old_name):
                            valves[key] = value[: -len(old_name)] + new_name
            status, _ = call(base, auth, "POST", f"/tools/id/{new_id}/valves/update", valves)
        if status != 200:
            failed += 1
            print(f"  FAILED   tool {new_id}: copying its Valves ({status}); tool {old_id} kept, set them by hand")
            continue
        print(f"  moved    tool {old_id} -> {new_id} (Valves and access)")
        status, result = call(base, auth, "DELETE", f"/tools/id/{old_id}/delete")
        if status != 200:
            failed += 1
            print(f"  FAILED   removing tool {old_id}: {status} {(result or {}).get('detail', '') if isinstance(result, dict) else ''}")
    # Models and presets that still name an old id (every run: also after a restored backup's models).
    seen, entries = set(), []
    for path in ("/models/base", "/models/export"):
        status, models = call(base, auth, "GET", path)
        for m in models if status == 200 and isinstance(models, list) else []:
            if m.get("id") not in seen:
                seen.add(m.get("id"))
                entries.append(m)
    for m in entries:
        meta = m.get("meta") or {}
        tool_ids = meta.get("toolIds") or []
        if not any(t in RENAMED_TOOLS for t in tool_ids):
            continue
        meta["toolIds"] = [RENAMED_TOOLS.get(t, t) for t in tool_ids]
        body = {"id": m["id"], "base_model_id": m.get("base_model_id"), "name": m["name"], "meta": meta,
                "params": m.get("params") or {}, "access_grants": grants(m), "is_active": m.get("is_active", True),
                "commit_message": "Renamed tools (LocalLLM repo)"}
        status, result = call(base, auth, "POST", "/models/model/update", body)
        if status == 200:
            print(f"  moved    model {m['id']}: its tools -> {', '.join(meta['toolIds'])}")
        else:
            failed += 1
            print(f"  FAILED   model {m['id']}: {status} {(result or {}).get('detail', '') if isinstance(result, dict) else ''}")
    return failed


def main() -> int:
    if len(sys.argv) not in (4, 5):
        sys.exit(__doc__)
    base, db_path, key_path = sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3]
    auth = token(db_path, key_path)

    failed = migrate_tools(base, auth)
    for folder, kind in KINDS.items():
        for file in sorted((HERE / folder).glob("*.py")):
            item_id = file.stem
            label = f"{kind} {item_id}"
            content = file.read_text(encoding="utf-8")
            version = header(content, "version") or "?"
            status, current = call(base, auth, "GET", f"/{folder}/id/{item_id}")
            # Open WebUI answers an unknown id with 401 and this message, so tell it apart from a login failure.
            missing = status == 404 or (current or {}).get("detail") == NOT_FOUND

            if status == 200 and current:
                if replace_imports(content) == (current.get("content") or ""):
                    print(f"  same     {label} ({version})")
                    continue
                old = header(current.get("content") or "", "version") or "?"
                body = {"id": item_id, "name": current["name"], "content": content,
                        "meta": current.get("meta") or {}, "commit_message": "Synced from the LocalLLM repo"}
                status, result = call(base, auth, "POST", f"/{folder}/id/{item_id}/update", body)
                if status == 200:
                    print(f"  updated  {label} ({old} -> {version})")
                else:
                    failed += 1
                    print(f"  FAILED   {label}: {status} {result.get('detail') if result else ''}")
            elif missing:
                title = header(content, "title") or item_id
                body = {"id": item_id, "name": title, "content": content,
                        "meta": {"description": header(content, "description")},
                        "commit_message": "Created from the LocalLLM repo"}
                status, result = call(base, auth, "POST", f"/{folder}/create", body)
                if status == 200:
                    print(f"  created  {label} ({version})")
                    if item_id in GLOBAL_FILTERS:
                        pass  # switched on below
                    elif kind == "function":
                        print("           It is switched off: turn it on in Admin Panel > Functions"
                              + (" (install/chat-types.sh does it)." if item_id == "protected_chat" else "."))
                    elif item_id not in ("protected_lookup", GATEWAY_TOOL):  # their Valves' defaults fit the local setup
                        print("           Set its Valves in Workspace > Tools.")
                else:
                    failed += 1
                    print(f"  FAILED   {label}: {status} {result.get('detail') if result else ''}")
            else:
                failed += 1
                print(f"  FAILED   {label}: {status} {current.get('detail') if current else ''}")
    failed += global_filters(base, auth)
    failed += gateway_valves(base, auth)
    failed += document_search(base, auth, lambda: token(db_path, key_path))
    auth = token(db_path, key_path)  # the re-indexing may have outlasted the token
    if len(sys.argv) == 5:
        failed += migrate_alias(base, auth, sys.argv[4])
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
