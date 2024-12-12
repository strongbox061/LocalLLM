"""
title: Answer Timer
author: LocalLLM
version: 0.3.0
description: Adds how long each answer took as a small line at the end of the reply. The line is removed again before the chat is sent to the model, so it never uses context.
"""

import re
import time

from pydantic import BaseModel, Field

TIMER_LINE_RE = re.compile(r"\n*\*⏱ Answered in [^*\n]*\*\s*$")


def _fmt(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} s"
    minutes, s = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} min {s:02d} s"
    hours, m = divmod(minutes, 60)
    return f"{hours} h {m:02d} min"


def _strip(content):
    if isinstance(content, str):
        return TIMER_LINE_RE.sub("", content)
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                part["text"] = TIMER_LINE_RE.sub("", part["text"])
    return content


class Filter:
    class Valves(BaseModel):
        priority: int = Field(
            default=0, description="Order among filters (lower runs first)."
        )
        debug: bool = Field(
            default=False,
            description="Print what the filter does to the Open WebUI terminal.",
        )

    def __init__(self):
        self.valves = self.Valves()
        self._start = {}  # chat id -> start time of the answer being written

    def _log(self, text: str):
        if self.valves.debug:
            print(f"[answer-timer] {text}", flush=True)

    @staticmethod
    def _chat(metadata, body) -> str:
        md = metadata or {}
        return str(md.get("chat_id") or (body or {}).get("chat_id") or "default")

    async def inlet(self, body: dict, __metadata__: dict = None) -> dict:
        # Remove earlier timer lines so the model never sees them.
        for m in body.get("messages", []):
            if m.get("role") == "assistant":
                m["content"] = _strip(m.get("content"))
        now = time.time()
        self._start = {k: t for k, t in self._start.items() if now - t < 7200}
        chat = self._chat(__metadata__, body)
        self._start[chat] = now
        self._log(f"inlet chat={chat}")
        return body
