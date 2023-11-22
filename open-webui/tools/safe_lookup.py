"""
title: Protected Lookup
author: LocalLLM
version: 0.2.0
description: The only way out of a protected chat (a chat type made by install/chat-types.sh). Searches only the websites chosen for that chat type, through the local search gateway (same services, cache and usage counts as Search Gateway), and redacts personal data before sending queries to the LLM.
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
    r"(?<![\w-])(?:\d+\s+)?(?:[A-Z][a-z]+\s+){1,3}"
    r"(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Dr|Drive|Ln|Lane|Pl|Place|"
    r"Way|Ct|Court|Cir|Circle|Pkwy|Parkway)[\s.,#]?\s*(?:No\.?[\s.]?\d+)?",
    re.I,
)

# Capitalised words that are never names, in any chat type: function words, titles, months and days. A chat type adds
# its own (institutions, places) in "allowed_terms".
COMMON_CAPITALISED = set(
    {
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will", "would",
        "could", "should", "may", "might", "shall", "can", "need", "dare",
        "ought", "used", "this", "that", "these", "those", "i", "you", "he",
        "she", "it", "we", "they", "me", "him", "her", "us", "them",
        "my", "your", "his", "its", "our", "their", "january", "february",
        "march", "april", "may", "june", "july", "august", "september",
        "october", "november", "december", "monday", "tuesday", "wednesday",
        "thursday", "friday", "saturday", "sunday", "mr", "mrs", "ms",
        "dr", "prof", "sr", "jr", "st",
    }
)

HONORIFIC_RE = re.compile(r"\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St)\.?\s*", re.I)
WORD_RE = re.compile(r"[^\W\d_][\w'-]*", re.U)

MAX_CHAT_TEXT = 3_000_000  # characters of documents and messages read for names and numbers


def fold(text: str) -> str:
    """Normalize unicode text: lowercase, strip combining marks, collapse whitespace."""
    text = unicodedata.normalize("NFKD", text).lower()
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.split())


def chat_type_of(meta) -> dict:
    """Return the chat-type config from metadata, or None."""
    if isinstance(meta, dict):
        return meta.get(META_KEY) or meta.get("chat_type")
    if isinstance(meta, str):
        try:
            return json.loads(meta).get(META_KEY)
        except (json.JSONDecodeError, AttributeError):
            return None
    return None


def clean_sites(sites) -> list:
    """Validate and deduplicate site patterns; returns a sorted list of strings."""
    if not sites:
        return []
    cleaned = set()
    for s in sites:
        if isinstance(s, str):
            cleaned.add(s.strip().lower())
    return sorted(cleaned)


def on_allowed_site(url: str, sites: list) -> bool:
    """Check whether a URL matches any of the allowed site patterns."""
    if not sites or not url:
        return False
    parsed = urlparse(url)
    host = parsed.hostname or ""
    for site in sites:
        if site.startswith("*.") and host.endswith(site[2:]):
            return True
        if site.startswith("http") and urlparse(site).hostname == host:
            return True
        if site == host:
            return True
    return False


def _text_of(content) -> str:
    """Extract plain text from various content types (string, bytes, dict)."""
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="replace")
    if isinstance(content, dict):
        return content.get("text", content.get("content", ""))
    if isinstance(content, str):
        return content
    return str(content or "")


def names_in(text: str) -> set:
    """Extract likely proper-name tokens from text (capitalised words outside common set)."""
    words = WORD_RE.findall(fold(text))
    return {w for w in words if w.istitle() and w.lower() not in COMMON_CAPITALISED and len(w) > 1}


def numbers_in(text: str) -> set:
    """Extract all numeric tokens from text."""
    return set(re.findall(r"\d[\d.,]*", text))


def _spans(regex, text):
    """Return list of (start, end) spans for regex matches in text."""
    return [(m.start(), m.end()) for m in regex.finditer(text)]


def _inside(span, spans) -> bool:
    """Check whether span is fully contained within any of the other spans."""
    start, end = span
    for s, e in spans:
        if s <= start and end <= e:
            return True
    return False


def check_query(query: str, cfg: dict, chat_text: str = ""):
    """
    Validate a search query against protected-chat rules.
    
    Returns (cleaned_query, allowed_sites) or raises ValueError on policy violation.
    
    Parameters:
        query: The raw user query string.
        cfg: Chat-type configuration dict with keys like 'allowed_sites', 'patterns'.
        chat_text: Accumulated text from the conversation for context-aware redaction.
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("Empty or invalid query")

    allowed_sites = clean_sites(cfg.get("allowed_sites", []))
    patterns = cfg.get("patterns", [])

    # Collect all personal data spans from query and accumulated chat text
    all_text = query + " " + (chat_text or "")[:MAX_CHAT_TEXT]
    redacted = fold(all_text)

    sensitive_spans = []
    for regex in [EMAIL_RE, PHONE_RE, ADDRESS_RE]:
        for span in _spans(regex, redacted):
            if not _inside(span, sensitive_spans):
                sensitive_spans.append(span)

    # Redact personal data from the query
    parts = []
    last_end = 0
    for start, end in sorted(sensitive_spans):
        parts.append(redacted[last_end:start])
        parts.append("[REDACTED]")
        last_end = end
    parts.append(redacted[last_end:])
    cleaned_query = "".join(parts).strip()

    if not cleaned_query:
        raise ValueError("Query contains only personal data")

    return cleaned_query, allowed_sites


class Tools(BaseModel):
    """Configuration for external tool access in protected chats."""
    search_gateway_url: str = "http://localhost:8080/search"
    max_results: int = Field(default=5, ge=1, le=20)
    timeout_seconds: int = Field(default=10, ge=1)

    class Config:
        frozen = True


async def _maybe(value):
    """Wrap a sync value in an awaitable; pass through awaitables unchanged."""
    if asyncio.isfuture(value) or hasattr(value, "__await__"):
        return await value
    return value
