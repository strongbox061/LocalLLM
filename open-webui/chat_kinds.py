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
