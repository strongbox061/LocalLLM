"""
title: Old Pages
author: LocalLLM
version: 1.1.0
description: Once a chat gets long, the text of web pages read for earlier answers is replaced by a short note with the page's address before the chat goes to the model. The answer being written keeps its pages in full, your messages and the answers stay as they are, and the model can read a page again when you ask about something its answer left out. Without it, every page is sent again with each new message, so research chats compact after a few messages.
"""

import json
import os

from pydantic import BaseModel, Field


NOTE = (
    "[Page text removed to save space: {url} ({chars} characters). What you used from it is in your answer above. "
    "If the user asks about something that isn't there, read this page again with {tool}{again}.]"
)
NOTE_START = "[Page text removed to save space:"
IMAGE_NOTE = "Here are the images from the tool results above."  # Open WebUI's own user message after tool images


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(p.get("text") or "") for p in content if isinstance(p, dict))
    return ""


def _chars(message: dict) -> int:
    """Characters the model reads for this message (its text, thinking and tool calls)."""
    n = len(_text(message.get("content"))) + len(str(message.get("reasoning_content") or ""))
    for call in message.get("tool_calls") or []:
        n += len(str((call.get("function") or {}).get("arguments") or ""))
    return n


def _is_prompt(message: dict) -> bool:
    return message.get("role") == "user" and not _text(message.get("content")).startswith(IMAGE_NOTE)


class Filter:
    class Valves(BaseModel):
        priority: int = Field(default=0, description="Order among filters (lower runs first).")
        tools: str = Field(
            default="read_page, fetch_url",
            description="Tools whose results count as pages (comma-separated). Search results are short and stay.",
        )
        start_at_tokens: int = Field(
            default=0,
            description=(
                "Start once the chat, counted with every page in full, is about this many tokens (4 characters each). "
                "0: 40% of Open WebUI's compaction threshold (CONTEXT_COMPACTION_TOKEN_THRESHOLD, set by "
                "services/open-webui.sh), 20,000 with a 64K slot. Below it nothing changes, so llama-server's cache "
                "is reused as it is and follow-up questions still have the pages."
            ),
        )
        min_chars: int = Field(default=1500, ge=1, description="Pages shorter than this stay as they are.")
        debug: bool = Field(default=False, description="Print what the filter does to the Open WebUI terminal.")

    def __init__(self):
        self.valves = self.Valves()

    def _start_tokens(self) -> int:
        if self.valves.start_at_tokens > 0:
            return self.valves.start_at_tokens
        try:
            threshold = int(os.environ.get("CONTEXT_COMPACTION_TOKEN_THRESHOLD") or 0)
        except ValueError:
            threshold = 0
        return threshold * 2 // 5 if threshold > 0 else 20000

    def request(self, body: dict) -> dict:
        """Runs before every call to the model, including each round of tool calls within an answer."""
        messages = body.get("messages")
        if not isinstance(messages, list):
            return body
        prompt = max((i for i, m in enumerate(messages) if isinstance(m, dict) and _is_prompt(m)), default=-1)
        if prompt <= 0:
            return body  # nothing before the current message
        # Counted with the pages in full, so the decision never flips back once a chat has grown past it.
        tokens = sum(_chars(m) for m in messages if isinstance(m, dict) and m.get("role") != "system") // 4
        if tokens < self._start_tokens():
            return body

        tools = {t.strip() for t in self.valves.tools.split(",") if t.strip()}
        calls = {}  # tool call id -> (tool name, URL)
        for m in messages[:prompt]:
            for call in (m.get("tool_calls") or []) if isinstance(m, dict) else []:
                fn = call.get("function") or {}
                if fn.get("name") in tools:
                    call_id = call.get("id")
                    if not call_id:
                        continue
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except (TypeError, ValueError):
                        args = {}
                    args = args if isinstance(args, dict) else {}
                    try:
                        part = int(args.get("part") or 1)
                    except (TypeError, ValueError):
                        part = 1
                    again = f" (part={part})" if part > 1 else ""
                    calls[call_id] = (fn["name"], str(args.get("url") or "the page"), again)

        removed = saved = 0
        for m in messages[:prompt]:
            if not isinstance(m, dict) or m.get("role") != "tool" or m.get("tool_call_id") not in calls:
                continue
            text = _text(m.get("content"))
            if len(text) < self.valves.min_chars or text.startswith(NOTE_START):
                continue
            tool, url, again = calls[m["tool_call_id"]]
            m["content"] = NOTE.format(url=url, chars=f"{len(text):,}", tool=tool, again=again)
            removed += 1
            saved += len(text) - len(m["content"])
        if self.valves.debug:
            print(f"[old-pages] chat ~{tokens} tokens: {removed} earlier page(s) shortened, ~{saved // 4} tokens saved",
                  flush=True)
        return body
