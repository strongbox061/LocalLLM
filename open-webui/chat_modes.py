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
        if key not in ct:
            problems.append(f"missing required key: {key}")
    return ct, problems


def preset_id(ct: dict) -> str:
    """Return the Open WebUI preset ID from a chat type dict."""
    return f"chat-{ct['id']}"


def knowledge_marker(ct: dict) -> str:
    """Return the knowledge base name suffix for this chat type's sources."""
    return f"{ct.get('name', ct['id'])} - sources"


def build_preset(ct: dict, alias: str, user_ids: list, knowledge: dict | None) -> dict:
    """Build the Open WebUI preset dict from a chat type definition."""
    capabilities = dict(CAPABILITIES)

    preset = {
        "name": f"chat-{ct['id']}",
        "model": alias,
        "description": DESCRIPTION_NOTE,
        "prompt": ct.get("system_prompt", ""),
        "prefix_prompt": "",
        "suffix_prompt": "",
        "images": capabilities["file_upload"],
        "citations": capabilities["citations"],
        "vision": capabilities["vision"],
        "web_search": capabilities["web_search"],
        "image_generation": capabilities["image_generation"],
        "code_interpreter": capabilities["code_interpreter"],
        "memory": capabilities["memory"],
        "terminal": capabilities["terminal"],
        "tools": {k: v for k, v in BUILTIN_TOOLS.items() if v},
        "metadata": {
            "protected_chat": {
                "user_ids": user_ids,
                "allowed_sites": ct.get("allowed_sites", []),
                "patterns": ct.get("patterns", []),
            }
        },
    }

    if knowledge:
        preset["knowledge"] = knowledge

    return preset


def grants_of(model: dict) -> list:
    """Return list of user IDs that have access to this model."""
    return model.get("grants", [])


def differences(want: dict, have: dict) -> list:
    """Compare a wanted preset against an existing one and return the differences."""
    diffs = []
    for key in want:
        if key not in have:
            diffs.append(f"missing key: {key}")
        elif want[key] != have[key]:
            diffs.append(f"{key}: want={want[key]!r} have={have[key]!r}")
    for key in have:
        if key not in want:
            diffs.append(f"extra key: {key}")
    return diffs


# ---------------------------------------------------------------- downloading sources


class _Text(HTMLParser):
    """Minimal HTML-to-text converter that preserves line breaks."""

    def __init__(self):
        super().__init__()
        self._parts = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True
        elif tag in ("br", "p", "div", "h1", "h2", "h3", "li"):
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False
        elif tag in ("p", "div", "h1", "h2", "h3", "li", "br"):
            self._parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts).strip()


def html_to_text(raw: str) -> str:
    """Convert HTML content to plain text."""
    parser = _Text()
    parser.feed(raw)
    return parser.get_text()


def download(url: str) -> str:
    """Download a URL and return its text content."""
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        print(f"error downloading {url}: {exc}", file=sys.stderr)
        return ""
    return raw


def pages(url: str) -> list:
    """Fetch a URL and return a list of text pages extracted from it."""
    raw = download(url)
    if not raw:
        return []

    # Simple extraction: split on headings or long blank sequences
    text = html_to_text(raw)
    parts = re.split(r"\n{3,}", text)
    return [p.strip() for p in parts if len(p.strip()) > 20]
