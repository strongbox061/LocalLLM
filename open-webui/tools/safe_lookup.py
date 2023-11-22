"""
title: Protected Lookup
author: LocalLLM
version: 0.2.0
description: The only way out of a protected chat (a chat type made by install/chat-types.sh). Searches only the websites chosen for that chat type, through the local search gateway (same services, cache and usage counts as Search Gateway), and reads the best page on those sites. Every query is checked first: names and numbers from this chat's documents and messages, emails, phone numbers, addresses, long numbers and the chat type's own patterns are removed. The final query is shown in a confirmation box and nothing is sent without the user's click. With no allowed sites in the chat type (a hand edit dropped them), every lookup is refused.
"""

import asyncio
import json
import re
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from pydantic import BaseModel, Field

META_KEY = "protected_chat"

# Personal data that is never sent, in any chat type. The chat type adds its own formats ("patterns").
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# A phone number: 7 or more digits with optional spaces, dots, dashes, brackets, a leading +.
PHONE_RE = re.compile(r"(?<![\w-])\+?\(?\d(?:[\s().-]*\d){6,}(?![\w-])")
# Any group of 5 or more digits (thousand dots allowed: 1.020.304), outside a public reference.
LONG_NUMBER_RE = re.compile(r"(?<![\w-])\d{1,3}(?:\.\d{3}){2,}(?![\w-])|(?<![\w-])\d{5,}(?![\w-])")
SHORT_NUMBER_RE = re.compile(r"(?<![\w.-])\d{3,4}(?![\w-])")
YEAR_RE = re.compile(r"^(1[89]\d\d|20\d\d)$")
# Street addresses: a street word, a number, and usually "#"/"No." and more numbers.
ADDRESS_RE = re.compile(
    r"(?i)\b(?:calle|carrera|cra|kra|cr|cl|avenida|av|diagonal|dg|transversal|tv|autopista|street|st|avenue|ave|"
    r"road|rd|boulevard|blvd|lane|ln|drive|dr)\.?\s*\d+\s*[a-z]?\b(?:\s*(?:#|no\.?|n[º°o]\.?)\s*\d+[a-z]?(?:\s*-\s*\d+)?)?"
    r"|\b\d+\s+[A-Z][a-z]+\s+(?:Street|St|Avenue|Ave|Road|Rd|Lane|Ln|Drive|Boulevard|Blvd)\b"
)

# Capitalised words that are never names, in any chat type: function words, titles, months and days. A chat type adds
# its own (institutions, places) in "allowed_terms".
COMMON_CAPITALISED = set(
    "el la los las un una de del y en por para con sin según segun sobre ante entre the a an of and in on for with "
    "to by from at under señor señora señores doctor doctora don doña sr sra dr dra mr mrs ms "
    "enero febrero marzo abril mayo junio julio agosto septiembre setiembre octubre noviembre diciembre lunes "
    "martes miércoles miercoles jueves viernes sábado sabado domingo january february march april may june july "
    "august september october november december monday tuesday wednesday thursday friday saturday sunday".split()
)

HONORIFIC_RE = re.compile(
    r"(?i)\b(?:señor|señora|sr|sra|srta|doctor|doctora|dr|dra|don|doña|mr|mrs|ms|miss|mister)\.?\s+"
    r"((?:[A-ZÁÉÍÓÚÑÜ][\wáéíóúñü'-]+\s*){1,4})"
)
WORD_RE = re.compile(r"[^\W\d_][\w'-]*", re.U)
MAX_CHAT_TEXT = 3_000_000  # characters of documents and messages read for names and numbers

def fold(text: str) -> str:
    """Lower case without accents, for comparisons."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))


def chat_type_of(meta) -> dict:
    """The chat type's settings from a preset's metadata, or {} when there are none."""
    if meta is None:
        return {}
    if not isinstance(meta, dict):
        meta = meta.model_dump() if hasattr(meta, "model_dump") else dict(getattr(meta, "__dict__", {}))
    cfg = meta.get(META_KEY)
    return cfg if isinstance(cfg, dict) else {}


def clean_sites(sites) -> list:
    out = []
    for s in sites or []:
        s = str(s).strip().lower().lstrip(".")
        s = re.sub(r"^https?://", "", s).split("/")[0]
        if re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", s) and s not in out:
            out.append(s)
    return out


def on_allowed_site(url: str, sites: list) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in ("http", "https") or not host:
        return False
    return any(host == s or host.endswith("." + s) for s in sites)


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and isinstance(p.get("text"), str))
    return ""


def names_in(text: str) -> set:
    """Folded words that look like names in the chat's text: capitalised in the middle of a sentence, never written in
    lower case anywhere, and not a common capitalised word; plus the words after a title (Sr., Dra., Mr. ...)."""
    lower_seen, capital = set(), set()
    for m in WORD_RE.finditer(text):
        w = m.group(0)
        if len(w) < 3:
            continue
        f = fold(w)
        if w[0].islower():
            lower_seen.add(f)
            continue
        if not w[0].isupper():
            continue
        before = text[max(0, m.start() - 3):m.start()]
        if m.start() == 0 or re.search(r"(?:[.!?:;\n¿¡\"'«(]|^)\s*$", before):
            continue  # sentence start: any word is capitalised there
        if w.isupper():
            continue  # headings and acronyms (CONTRACT, ABC); a name only in capitals is left to the confirmation
        capital.add(f)
    names = {f for f in capital - lower_seen if f not in COMMON_CAPITALISED}
    for m in HONORIFIC_RE.finditer(text):
        names.update(fold(w) for w in WORD_RE.findall(m.group(1)) if len(w) >= 3 and fold(w) not in COMMON_CAPITALISED)
    return names


def numbers_in(text: str) -> set:
    return {re.sub(r"\D", "", n) for n in re.findall(r"\d[\d.\-/ ]*\d|\d", text) if len(re.sub(r"\D", "", n)) >= 3}


def _spans(regex, text):
    """Return list of (start, end) spans for regex matches in text."""
    return [(m.start(), m.end()) for m in regex.finditer(text)]


def _inside(span, spans) -> bool:
    return any(a <= span[0] and span[1] <= b for a, b in spans)


def check_query(query: str, cfg: dict, chat_text: str = ""):
    """(clean query, removed items as (text, reason), refusal or "")."""
    query = " ".join(str(query or "").split())
    removed, cut = [], []

    def drop(a, b, why):
        if not any(a < y and x < b for x, y in cut):
            cut.append((a, b))
            removed.append((query[a:b].strip(), why))

    # Public references, whose numbers stay: a number right after one of the chat type's "reference_words" ("rule 12",
    # "section 4 of 2003"), or a match of its "allowed_patterns".
    words = [str(w).strip() for w in cfg.get("reference_words") or [] if str(w).strip()]
    safe = []
    if words:
        ref_re = re.compile(
            r"(?i)\b(?:" + "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)) + r")\.?\s+"
            r"(?:n[º°o]\.?\s*)?\d+(?:[.,]\d+)*(?:\s*(?:-|,|y|and)\s*\d+)*(?:\s+(?:de|del|of)\s+\d{4})?"
        )
        safe = _spans(ref_re, query)
    for p in cfg.get("allowed_patterns") or []:
        try:
            safe += _spans(re.compile(p.get("regex", "") if isinstance(p, dict) else str(p)), query)
        except re.error:
            pass

    # Always removed, even inside a reference: the chat type's own patterns, emails, addresses, phone numbers.

    for p in cfg.get("patterns") or []:
        regex = p.get("regex", "") if isinstance(p, dict) else str(p)
        name = p.get("name", "personal data") if isinstance(p, dict) else "personal data"
        try:
            for a, b in _spans(re.compile(regex), query):
                drop(a, b, name)
        except re.error:
            return "", removed, f"The chat type's pattern {regex!r} is not a valid regular expression; fix the file and rerun install/chat-types.sh."
    for a, b in _spans(EMAIL_RE, query):
        drop(a, b, "email")
    for a, b in _spans(ADDRESS_RE, query):
        drop(a, b, "address")
    for a, b in _spans(PHONE_RE, query):
        if not _inside((a, b), safe):
            drop(a, b, "phone or ID number")
    for a, b in _spans(LONG_NUMBER_RE, query):
        if not _inside((a, b), safe):
            drop(a, b, "long number")

    chat_numbers = numbers_in(chat_text) if chat_text else set()
    for a, b in _spans(SHORT_NUMBER_RE, query):
        n = query[a:b]
        if not _inside((a, b), safe) and not YEAR_RE.match(n) and n in chat_numbers:
            drop(a, b, "number from this chat")

    names = names_in(chat_text) if chat_text else set()
    for m in HONORIFIC_RE.finditer(query):
        names.update(fold(w) for w in WORD_RE.findall(m.group(1)) if fold(w) not in COMMON_CAPITALISED)
    allowed_terms = {fold(t) for t in cfg.get("allowed_terms") or []}
    for m in WORD_RE.finditer(query):
        f = fold(m.group(0))
        if f in names and f not in allowed_terms and not _inside((m.start(), m.end()), safe):
            drop(m.start(), m.end(), "name from this chat")

    out, last = [], 0
    for a, b in sorted(cut):
        out.append(query[last:a])
        last = b
    out.append(query[last:])
    clean = " ".join("".join(out).split())
    clean = re.sub(r"\s+([,.;:])", r"\1", clean).strip(" ,;:-")
    clean = re.sub(r"\b(?:site|inurl|intitle):\S*", "", clean).strip()  # the tool adds the site filter itself
    words = [w for w in WORD_RE.findall(clean) if len(w) >= 3]
    if len(words) < 2:
        return clean, removed, (
            "Too little is left of the query after removing personal data. Ask with general terms only (the topic, "
            "the rule or reference), never names, numbers or details from the user's documents."
        )
    return clean, removed, ""


async def _maybe(value):
    return await value if hasattr(value, "__await__") else value


class Tools:
    class Valves(BaseModel):
        research_url: str = Field(
            default="http://127.0.0.1:8090",
            description="The local search gateway (install/search-gateway.sh), which searches with Search Gateway's services and reads pages.",
        )
        max_results: int = Field(default=5, description="Results on the allowed sites listed for the model.")
        max_page_chars: int = Field(default=12000, description="Characters of the best page given to the model.")
        timeout_seconds: int = Field(default=90, description="Wait at most this long for a search or a page.")

    def __init__(self):
        self.valves = self.Valves()
        self.citation = False  # the tool sends its own citations

    # ------------------------------------------------------------------ what the chat holds

    @staticmethod
    async def _chat_type(__metadata__, __model__) -> dict:
        """The calling preset's chat type, read from Open WebUI's database (a hand edit is seen at once). Tools get the
        task model as __model__, so the preset is found by the chat's model id."""
        model_id = (__metadata__ or {}).get("model_id") or ""
        if model_id:
            try:
                from open_webui.models.models import Models

                model = await _maybe(Models.get_model_by_id(model_id))
                if model is not None:
                    return chat_type_of(getattr(model, "meta", None))
            except ImportError:
                pass
        if __model__ and (not model_id or __model__.get("id") == model_id):
            return chat_type_of(((__model__.get("info") or {}).get("meta")) or {})
        return {}

    @staticmethod
    async def _chat_text(messages, files) -> str:
        """The person's messages and the documents in this chat (uploaded files and Knowledge added with #)."""
        parts = [_text_of(m.get("content")) for m in messages or [] if isinstance(m, dict) and m.get("role") == "user"]
        size = sum(len(p) for p in parts)
        try:
            from open_webui.models.files import Files
            from open_webui.models.knowledge import Knowledges
        except ImportError:
            Files = Knowledges = None
        for item in files or []:
            if size > MAX_CHAT_TEXT or not isinstance(item, dict):
                break
            texts = []
            content = (((item.get("file") or {}).get("data")) or {}).get("content")
            if isinstance(content, str):
                texts.append(content)
            elif Files and item.get("type", "file") == "file" and item.get("id"):
                f = await _maybe(Files.get_file_by_id(item["id"]))
                texts.append(((getattr(f, "data", None) or {}).get("content")) or "")
            elif Knowledges and item.get("type") == "collection" and item.get("id"):
                for f in await _maybe(Knowledges.get_files_by_id(item["id"])) or []:
                    texts.append(((getattr(f, "data", None) or {}).get("content")) or "")
            for t in texts:
                parts.append(t)
                size += len(t)
        return "\n".join(parts)[:MAX_CHAT_TEXT]

    # ------------------------------------------------------------------ the search gateway

    def _get(self, path: str, params: dict) -> dict:
        url = f"{self.valves.research_url.rstrip('/')}{path}?{urlencode(params)}"
        try:
            with urlopen(Request(url), timeout=self.valves.timeout_seconds) as r:
                return json.loads(r.read().decode("utf-8"))
        except HTTPError as e:
            try:
                return {"error": json.loads(e.read()).get("error") or f"HTTP {e.code}"}
            except Exception:
                return {"error": f"HTTP {e.code}"}
        except (URLError, OSError, ValueError) as e:
            return {"error": f"the search gateway isn't answering ({type(e).__name__})"}

    async def _search(self, q: str) -> dict:
        return await asyncio.to_thread(self._get, "/search", {"q": q, "app": "Protected Lookup"})

    async def _read(self, url: str) -> dict:
        return await asyncio.to_thread(self._get, "/read", {"url": url})

    @staticmethod
    async def _status(emitter, text: str, done: bool = False):
        if emitter:
            await emitter({"type": "status", "data": {"description": text, "done": done}})

    # ------------------------------------------------------------------ the tool

    async def protected_lookup(
        self,
        query: str,
        __metadata__: dict = None,
        __model__: dict = None,
        __messages__: list = None,
        __files__: list = None,
        __event_emitter__=None,
        __event_call__=None,
    ) -> str:
        """
        Look up public information (rules, official texts, recent changes, official guidance) on the websites allowed
        for this chat. The user must approve the query before anything is sent.
        Use it only for what the documents and the Knowledge base don't answer, such as recent changes.
        Write a short query with general terms only: the topic, the rule and its number. Never put names, ID or
        reference numbers, addresses, amounts or other details from the user's messages or documents in it.
        :param query: A short search query in general terms, e.g. "safety rule 12 latest version".
        """
        cfg = await self._chat_type(__metadata__, __model__)
        sites = clean_sites(cfg.get("allowed_sites"))
        if not sites:
            return (
                "Lookups are switched off: this chat type has no allowed websites (its settings are missing). Answer "
                "from the documents and the Knowledge base, and tell the user that the administrator must run "
                "install/chat-types.sh again."
            )

        chat_text = await self._chat_text(__messages__, __files__)
        clean, removed, refusal = check_query(query, cfg, chat_text)
        if refusal:
            return "Lookup refused, nothing was sent. " + refusal
        site_filter = " OR ".join(f"site:{s}" for s in sites)
        final = f"{clean} {site_filter if len(sites) == 1 else '(' + site_filter + ')'}"

        if not __event_call__:
            return "Lookup refused, nothing was sent: the user can't be asked to approve it here (no browser session)."
        note = ""
        if removed:
            note = "\n\nRemoved as personal data: " + "; ".join(f"«{t}» ({why})" for t, why in removed)
        try:
            approved = await __event_call__(
                {
                    "type": "confirmation",
                    "data": {
                        "title": "Send this search to the internet?",
                        "message": (
                            f"Query: {final}\n\nOnly these websites: {', '.join(sites)}. The best result on them is "
                            f"then opened and read.{note}\n\nNothing else from this chat leaves the computer."
                        ),
                    },
                }
            )
        except Exception as e:
            return f"Lookup refused, nothing was sent: the confirmation box failed ({type(e).__name__})."
        if approved is not True:
            return "The user did not approve this lookup; nothing was sent. Answer without it, or ask the user."

        await self._status(__event_emitter__, f"Searching {', '.join(sites)}")
        data = await self._search(final)
        if data.get("error"):
            await self._status(__event_emitter__, "Search failed", done=True)
            return f"The search failed ({data['error']}). Say the lookup didn't work; don't guess what the sites say."
        results = [r for r in data.get("results") or [] if on_allowed_site(r.get("url", ""), sites)]
        dropped = len(data.get("results") or []) - len(results)
        results = results[: max(1, self.valves.max_results)]
        if not results:
            await self._status(__event_emitter__, "Nothing found on the allowed sites", done=True)
            return (
                f'Nothing found on {", ".join(sites)} for "{clean}"'
                + (f" ({dropped} results on other sites were dropped)" if dropped else "")
                + ". Try other general terms once, or say it wasn't found."
            )

        best = results[0]
        await self._status(__event_emitter__, f"Reading {best['url']}")
        page = await self._read(best["url"])
        text = str(page.get("text") or "")
        if page.get("error") or not text:
            page_part = f"The best page couldn't be read ({page.get('error') or 'empty'}); use the snippets below."
        else:
            if len(text) > self.valves.max_page_chars:
                text = text[: self.valves.max_page_chars] + f"\n\n[Page cut at {self.valves.max_page_chars} characters.]"
            page_part = f"Text of {best['url']}:\n\n{text}"
            if __event_emitter__:
                await __event_emitter__(
                    {
                        "type": "citation",
                        "data": {
                            "document": [text[:4000]],
                            "metadata": [{"source": best["url"], "name": best.get("title") or best["url"]}],
                            "source": {"name": best.get("title") or best["url"], "url": best["url"]},
                        },
                    }
                )
        await self._status(__event_emitter__, f"Read {urlparse(best['url']).hostname}", done=True)
        listing = "\n".join(
            f"{i}. {r.get('title') or r['url']}\n   {r['url']}\n   {(r.get('content') or '')[:300]}"
            for i, r in enumerate(results, 1)
        )
        return (
            f'Results on {", ".join(sites)} for "{clean}" (sent with the user\'s approval):\n\n{listing}\n\n{page_part}\n\n'
            "Quote the exact wording you rely on, with its link. If the page doesn't answer the question, say so."
        )
