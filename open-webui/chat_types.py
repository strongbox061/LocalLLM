"""
Builds Open WebUI's protected chats from the chat type files (README, "Protected chats in Open WebUI").
Run it with install/chat-types.sh, which uses Open WebUI's Python and logs in like sync.py.

Each <dir>/<id>.json becomes the preset (Workspace > Models) "chat-<id>": base model the profile's alias, the file's
system prompt and starter prompts, only the Protected Lookup tool, the Protected Chat filter, web search / image
generation / code interpreter / memory / terminal off, file upload / vision / citations on, the allowed sites and
patterns in its metadata (key "protected_chat"), shown to the file's users. A chat type with sources also gets its own
Knowledge base ("<name> - sources"), attached to the preset. The files are the source of truth: a preset edited by hand
is overwritten on the next run; a preset whose file is gone is reported, not deleted.

  --refresh-sources  also download each source's official pages into its Knowledge base (a changed text replaces the
                     old file; an unchanged one is left alone). A url with {n:03} is a numbered series of pages
                     (..._pr001.html, ..._pr002.html, ...), read until the first missing one.
  --dry-run          check the files and print the presets that would be sent; change nothing (no login needed)
  --only <id>        only that chat type (with or without --refresh-sources)
  --remove <id>      delete the preset chat-<id> and its Knowledge base (not the file)

Usage: python chat_types.py <Open WebUI URL> <webui.db> <.webui_secret_key> <model.json> <chat types dir> [options]
"""

import datetime
import hashlib
import html
import json
import re
import sys
import time
import uuid
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
DOMAIN_RE = re.compile(r"^[a-z0-9-]+(?:\.[a-z0-9-]+)+$")
FILTER_ID, TOOL_ID, META_KEY = "protected_chat", "protected_lookup", "protected_chat"
CAPABILITIES = {
    "vision": True, "file_upload": True, "citations": True, "status_updates": True, "builtin_tools": True,
    "web_search": False, "image_generation": False, "code_interpreter": False, "terminal": False, "memory": False,
}
# Open WebUI's built-in tools: only the ones that stay inside the chat (time, its files, its Knowledge, asking the
# user, a task list). Everything that reaches other chats, notes, schedules, webhooks, other models or new code is off.
BUILTIN_TOOLS = {
    "time": True, "files": True, "knowledge": True, "user_input": True, "tasks": True,
    "web_search": False, "image_generation": False, "code_interpreter": False, "memory": False, "chats": False,
    "notes": False, "channels": False, "automations": False, "calendar": False, "notifications": False,
    "subagents": False, "tools": False, "skills": False,
}
PROMPT_NOTE = (
    "\n\nThis is a protected chat: the documents and messages stay on this computer. The only way out is the "
    "protected_lookup tool, which searches only this chat type's allowed websites ({sites}) and asks the user to "
    "approve each query. Never put names, ID or reference numbers, addresses or other details from the documents in a "
    "query. If the user switches this chat to another model, the protection no longer applies to that chat."
)
DESCRIPTION_NOTE = " Protected: nothing leaves the computer but lookups you approve; switching to another model in a chat ends the protection there."
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) LocalLLM-chat-types"
MAX_PARTS = 400


# ---------------------------------------------------------------- chat type files


def load_chat_type(path: Path) -> tuple[dict, list]:
    """(chat type, problems). The chat type is usable only when there are no problems."""
    problems = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {}, [f"can't read it: {e}"]
    if not isinstance(data, dict):
        return {}, ["it isn't a JSON object"]
    ct = {k: v for k, v in data.items() if not k.startswith("_")}
    ct["id"] = path.stem
    if not ID_RE.match(path.stem):
        problems.append("the file name must be lower case letters, digits and dashes")
    for key in ("name", "system_prompt"):
        if not isinstance(ct.get(key), str) or not ct[key].strip():
            problems.append(f'"{key}" is missing')
    sites = ct.get("allowed_sites")
    if not isinstance(sites, list) or not sites:
        problems.append('"allowed_sites" must list at least one domain')
    else:
        bad = [s for s in sites if not isinstance(s, str) or not DOMAIN_RE.match(s.strip().lower())]
        if bad:
            problems.append(f"not a domain (write example.org, no https:// or path): {bad}")
        ct["allowed_sites"] = [s.strip().lower() for s in sites if isinstance(s, str)]
    for key in ("patterns", "allowed_patterns"):
        items = ct.get(key) or []
        if not isinstance(items, list):
            problems.append(f'"{key}" must be a list')
            continue
        for p in items:
            regex = p.get("regex") if isinstance(p, dict) else None
            try:
                re.compile(regex or "")
                if not regex:
                    raise re.error("empty")
            except (re.error, TypeError) as e:
                problems.append(f'{key}: {p!r} has no valid "regex" ({e})')
    for key in ("allowed_terms", "reference_words", "starter_prompts", "users"):
        if not isinstance(ct.get(key) or [], list) or not all(isinstance(x, str) for x in ct.get(key) or []):
            problems.append(f'"{key}" must be a list of strings')
    for s in ct.get("sources") or []:
        urls = s.get("urls") if isinstance(s, dict) else None
        if not isinstance(s, dict) or not s.get("name") or not isinstance(urls, list) or not urls:
            problems.append(f'sources: each needs "name" and "urls" (a list): {s!r}')
        elif not all(isinstance(u, str) and re.match(r"^https?://", u) for u in urls):
            problems.append(f"sources: {s['name']}: every url must start with http:// or https://")
    return ct, problems


def preset_id(ct: dict) -> str:
    return f"chat-{ct['id']}"


def knowledge_marker(ct: dict) -> str:
    return f"[chat type: {ct['id']}]"


def build_preset(ct: dict, alias: str, user_ids: list, knowledge: dict | None) -> dict:
    sites = ct["allowed_sites"]
    meta = {
        "description": ((ct.get("description") or "").strip() + DESCRIPTION_NOTE).strip(),
        "suggestion_prompts": [{"content": p, "title": ["", ""]} for p in ct.get("starter_prompts") or []] or None,
        "capabilities": dict(CAPABILITIES),
        "builtinTools": dict(BUILTIN_TOOLS),
        "toolIds": [TOOL_ID],
        "filterIds": [FILTER_ID],
        "defaultFeatureIds": [],
        "tags": [{"name": "protected"}],
        META_KEY: {
            "chat_type": ct["id"],
            "allowed_sites": sites,
            "patterns": ct.get("patterns") or [],
            "allowed_patterns": ct.get("allowed_patterns") or [],
            "allowed_terms": ct.get("allowed_terms") or [],
            "reference_words": ct.get("reference_words") or [],
        },
    }
    if knowledge:
        meta["knowledge"] = [{"id": knowledge["id"], "name": knowledge["name"],
                              "description": knowledge.get("description", ""), "type": "collection"}]
    return {
        "id": preset_id(ct),
        "base_model_id": alias,
        "name": ct["name"].strip(),
        "meta": meta,
        "params": {"system": ct["system_prompt"].strip() + PROMPT_NOTE.format(sites=", ".join(sites))},
        "access_grants": [{"principal_type": "user", "principal_id": u, "permission": "read"} for u in user_ids],
        "is_active": True,
    }


def grants_of(model: dict) -> list:
    keys = ("principal_type", "principal_id", "permission")
    return sorted(tuple(g.get(k) for k in keys) for g in model.get("access_grants") or [])


def differences(want: dict, have: dict) -> list:
    """What differs between the preset to send and the one in Open WebUI (only what this script sets)."""
    out = []
    for key in ("base_model_id", "name", "is_active"):
        if want.get(key) != have.get(key):
            out.append(key)
    if want["params"].get("system") != (have.get("params") or {}).get("system"):
        out.append("system prompt")
    have_meta = have.get("meta") or {}
    for key, value in want["meta"].items():
        if json.loads(json.dumps(value)) != have_meta.get(key):
            out.append(key)
    if "knowledge" not in want["meta"] and have_meta.get("knowledge"):
        out.append("knowledge")
    if grants_of(want) != grants_of(have):
        out.append("users")
    return out


# ---------------------------------------------------------------- downloading sources


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "nav", "header", "footer", "form", "button", "select", "svg"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section", "article",
             "blockquote", "pre", "dd", "dt", "ul", "ol"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(raw: str) -> str:
    parser = _Text()
    parser.feed(raw)
    text = html.unescape("".join(parser.parts)).replace("\xa0", " ")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def download(url: str) -> str:
    with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=120) as r:
        raw, ctype = r.read(), r.headers.get("Content-Type", "")
    m = re.search(r"charset=([\w-]+)", ctype) or re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", raw[:4096], re.I)
    charset = (m.group(1).decode() if isinstance(m.group(1), bytes) else m.group(1)) if m else "utf-8"
    try:
        text = raw.decode(charset)
    except (LookupError, UnicodeDecodeError):
        text = raw.decode("cp1252", errors="replace")
    return html_to_text(text) if "html" in ctype or re.search(r"<html|<body", text[:2000], re.I) else text


def pages(url: str) -> list:
    """A url's text; a url with "{n}" or "{n:03}" is a numbered series: parts 1, 2, 3... until the first missing one."""
    if "{n" not in url:
        return [download(url)]
    out = []
    for n in range(1, MAX_PARTS + 1):
        try:
            out.append(download(url.format(n=n)))
        except HTTPError as e:
            if e.code == 404 and out:
                return out
            raise
        time.sleep(0.5)  # be gentle with the official site
    raise RuntimeError(f"more than {MAX_PARTS} parts in {url}")


def source_document(source: dict) -> str:
    """The source's pages as one text with a header naming where it came from. The date stays out of the text, so
    an unchanged source is recognised as unchanged."""
    body = "\n\n".join(text for u in source["urls"] for text in pages(u))
    head = f"# {source['name']}\n\nOfficial text, downloaded from: {', '.join(source['urls'])}\n\n"
    return head + body + "\n"


def file_name(source: dict) -> str:
    return re.sub(r"[^\w.-]+", "-", source["name"].strip(), flags=re.U).strip("-") + ".md"


# ---------------------------------------------------------------- Open WebUI


def upload(base: str, auth: str, name: str, text: str):
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
            f"Content-Type: text/markdown\r\n\r\n").encode() + text.encode("utf-8") + f"\r\n--{boundary}--\r\n".encode()
    req = Request(f"{base}/api/v1/files/?process=true&process_in_background=false", data=body, method="POST",
                  headers={"Authorization": f"Bearer {auth}", "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urlopen(req, timeout=900) as r:
            return r.status, json.loads(r.read())
    except HTTPError as e:
        try:
            return e.code, {"detail": json.loads(e.read()).get("detail")}
        except Exception:
            return e.code, {"detail": e.reason}


def main() -> int:
    args, opts, only, remove = [], set(), None, None
    argv = sys.argv[1:]
    while argv:
        a = argv.pop(0)
        if a in ("--only", "--remove"):
            if not argv or not ID_RE.match(argv[0]):
                sys.exit(f"{a} needs a chat type id (lower case letters, digits, dashes)")
            only, remove = (argv.pop(0), remove) if a == "--only" else (only, argv.pop(0))
        elif a.startswith("--"):
            opts.add(a)
        else:
            args.append(a)
    if len(args) != 5 or opts - {"--refresh-sources", "--dry-run"} or (remove and (opts or only)):
        sys.exit(__doc__)
    base, db_path, key_path, model_json, folder = args
    base = base.rstrip("/")
    dry, refresh = "--dry-run" in opts, "--refresh-sources" in opts
    if remove:
        return remove_chat_type(base, db_path, key_path, remove)
    try:
        alias = json.loads(Path(model_json).read_text(encoding="utf-8")).get("alias") or "local"
    except (OSError, ValueError):
        alias = "local"

    files = sorted(Path(folder).glob("*.json"))
    if only:
        if not (Path(folder) / f"{only}.json").is_file():
            print(f"  FAILED   no chat type {only} ({folder}/{only}.json)")
            return 1
    if not files:
        print(f"  none     no chat types in {folder} (copy open-webui/chat-types/example.json there)")
        return 0
    chat_types, failed = [], 0
    for f in files:
        if only and f.stem != only:
            continue
        ct, problems = load_chat_type(f)
        if problems:
            failed += 1
            print(f"  FAILED   {f.name}: " + "; ".join(problems))
        else:
            chat_types.append(ct)
    if dry:
        for ct in chat_types:
            k = {"id": "<knowledge id>", "name": f"{ct['name']} - sources", "description": knowledge_marker(ct)}
            print(json.dumps(build_preset(ct, alias, [f"<id of {u}>" for u in ct.get("users") or []],
                                          k if ct.get("sources") else None), ensure_ascii=False, indent=1))
        return 1 if failed else 0

    from sync import call, token

    # sync.py's tokens last one minute and downloading the sources takes much longer: a new one for every request.
    def api(method, path, body=None, timeout=120):
        return call(base, token(db_path, key_path), method, path, body, timeout)

    def fresh_token():
        return token(db_path, key_path)

    # The filter and the tool come from install/open-webui-config.sh; the filter must be on, and not global.
    status, fn = api("GET", f"/functions/id/{FILTER_ID}")
    status_t, _ = api("GET", f"/tools/id/{TOOL_ID}")
    if status != 200 or status_t != 200:
        print("  FAILED   the Protected Chat filter or the Protected Lookup tool isn't in Open WebUI: run "
              "install/open-webui-config.sh first")
        return 1
    if not fn.get("is_active"):
        status, fn = api("POST", f"/functions/id/{FILTER_ID}/toggle")
        print(f"  {'switched' if status == 200 else 'FAILED  '} filter {FILTER_ID} on")
        failed += status != 200
    if fn.get("is_global"):
        status, fn = api("POST", f"/functions/id/{FILTER_ID}/toggle/global")
        print(f"  {'switched' if status == 200 else 'FAILED  '} filter {FILTER_ID} to these presets only (not global)")
        failed += status != 200

    status, users = api("GET", "/users/all")
    users = (users or {}).get("users", []) if status == 200 else []
    by_email = {(u.get("email") or "").lower(): u["id"] for u in users}
    status, kbs = api("GET", "/knowledge/")
    kbs = (kbs or {}).get("items", []) if status == 200 else []

    for ct in chat_types:
        pid = preset_id(ct)
        user_ids = []
        for email in ct.get("users") or []:
            if email.lower() in by_email:
                user_ids.append(by_email[email.lower()])
            else:
                print(f"  note     {ct['id']}: no Open WebUI account {email!r} yet; run this again once it exists")
        grants = [{"principal_type": "user", "principal_id": u, "permission": "read"} for u in user_ids]

        knowledge = None
        if ct.get("sources"):
            knowledge = next((k for k in kbs if knowledge_marker(ct) in (k.get("description") or "")), None)
            name = f"{ct['name'].strip()} - sources"
            desc = f"Official texts for the chat type {ct['name'].strip()}. Filled by install/chat-types.sh --refresh-sources. {knowledge_marker(ct)}"
            if knowledge is None:
                status, knowledge = api("POST", "/knowledge/create",
                                         {"name": name, "description": desc, "access_grants": grants})
                print(f"  {'created ' if status == 200 else 'FAILED  '} knowledge base {name!r}")
                if status != 200:
                    failed += 1
                    knowledge = None
            elif knowledge.get("name") != name or knowledge.get("description") != desc or \
                    grants_of(knowledge) != grants_of({"access_grants": grants}):
                status, result = api("POST", f"/knowledge/{knowledge['id']}/update",
                                      {"name": name, "description": desc, "access_grants": grants})
                print(f"  {'updated ' if status == 200 else 'FAILED  '} knowledge base {name!r}")
                failed += status != 200
                knowledge = {**knowledge, "name": name, "description": desc}
            if knowledge and refresh:
                failed += refresh_sources(base, fresh_token, ct, knowledge, api)

        want = build_preset(ct, alias, user_ids, knowledge)
        status, have = api("GET", f"/models/model?id={pid}")
        exists = status == 200 and have and have.get("id") == pid
        if exists:
            diff = differences(want, have)
            if not diff:
                print(f"  same     preset {pid}")
                continue
            body = {**want, "meta": {**(have.get("meta") or {}), **want["meta"]},
                    "commit_message": "Updated from the chat type file by the LocalLLM repo"}
            if "knowledge" not in want["meta"]:
                body["meta"].pop("knowledge", None)
            status, result = api("POST", "/models/model/update", body)
            print(f"  {'updated ' if status == 200 else 'FAILED  '} preset {pid} ({', '.join(diff)})"
                  + ("" if status == 200 else f": {status} {(result or {}).get('detail', '')}"))
        else:
            body = {**want, "commit_message": "Created from the chat type file by the LocalLLM repo"}
            status, result = api("POST", "/models/create", body)
            print(f"  {'created ' if status == 200 else 'FAILED  '} preset {pid}"
                  + ("" if status == 200 else f": {status} {(result or {}).get('detail', '')}"))
        failed += status != 200

    # Presets made from a chat type file that no longer exists: left alone, but said.
    status, models = api("GET", "/models/export")
    known = {preset_id(ct) for ct in chat_types} | {f"chat-{f.stem}" for f in files}
    for m in models if status == 200 and isinstance(models, list) else []:
        if META_KEY in (m.get("meta") or {}) and m["id"] not in known:
            print(f"  note     preset {m['id']} has no chat type file any more; delete it in Workspace > Models if "
                  "it's no longer wanted")
    return 1 if failed else 0


def remove_chat_type(base: str, db_path: str, key_path: str, ct_id: str) -> int:
    """Deletes the preset chat-<id> and its Knowledge base from Open WebUI (the file is the caller's business)."""
    from sync import call, token

    def api(method, path, body=None):
        return call(base, token(db_path, key_path), method, path, body)

    failed = 0
    pid = f"chat-{ct_id}"
    status, have = api("GET", f"/models/model?id={pid}")
    if status == 200 and have and have.get("id") == pid:
        status, result = api("POST", "/models/model/delete", {"id": pid})
        print(f"  {'removed ' if status == 200 else 'FAILED  '} preset {pid}"
              + ("" if status == 200 else f": {status} {(result or {}).get('detail', '')}"))
        failed += status != 200
    else:
        print(f"  none     preset {pid}")
    status, kbs = api("GET", "/knowledge/")
    marker = knowledge_marker({"id": ct_id})
    for k in (kbs or {}).get("items", []) if status == 200 else []:
        if marker in (k.get("description") or ""):
            status, result = api("DELETE", f"/knowledge/{k['id']}/delete")
            print(f"  {'removed ' if status == 200 else 'FAILED  '} knowledge base {k.get('name')!r}"
                  + ("" if status == 200 else f": {status} {(result or {}).get('detail', '')}"))
            failed += status != 200
    return 1 if failed else 0


def refresh_sources(base: str, fresh_token, ct: dict, knowledge: dict, api) -> int:
    failed = 0
    status, listing = api("GET", f"/knowledge/{knowledge['id']}/files")
    items = (listing or {}).get("items", []) if status == 200 else []
    current = {i.get("filename") or (i.get("meta") or {}).get("name"): i for i in items}
    for source in ct["sources"]:
        name = file_name(source)
        try:
            text = source_document(source)
        except Exception as e:
            failed += 1
            print(f"  FAILED   source {source['name']}: download failed ({type(e).__name__}: {e})")
            continue
        old = current.get(name)
        if old:
            status, data = api("GET", f"/files/{old['id']}/data/content")
            if status == 200 and hashlib.sha256((data or {}).get("content", "").strip().encode()).digest() == \
                    hashlib.sha256(text.strip().encode()).digest():
                print(f"  same     source {source['name']} ({len(text):,} characters)")
                continue
        status, f = upload(base, fresh_token(), name, text)
        if status != 200:
            failed += 1
            print(f"  FAILED   source {source['name']}: upload {status} {(f or {}).get('detail', '')}")
            continue
        status, result = api("POST", f"/knowledge/{knowledge['id']}/file/add", {"file_id": f["id"]})
        if status != 200:
            failed += 1
            print(f"  FAILED   source {source['name']}: adding it to the Knowledge base: {status} "
                  f"{(result or {}).get('detail', '')}")
            continue
        if old:
            api("POST", f"/knowledge/{knowledge['id']}/file/remove?delete_file=true", {"file_id": old["id"]})
        print(f"  {'updated ' if old else 'added   '} source {source['name']} ({len(text):,} characters, "
              f"{datetime.date.today().isoformat()})")
    return failed


if __name__ == "__main__":
    sys.exit(main())
