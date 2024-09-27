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


class Filter(BaseModel):
    """Timer filter for Open WebUI completions."""

    enabled: bool = Field(default=True)
    _start: dict = Field(default_factory=dict)  # chat id -> start time

    @staticmethod
    def _chat(metadata, body) -> str:
        md = metadata or {}
        return str(md.get("chat_id") or (body or {}).get("chat_id") or "default")

    def set_start(self, chat: str = "default"):
        """Record when the request started."""
        self._start[chat] = time.time()

    def get_elapsed(self, chat: str = "default") -> float:
        """Return elapsed seconds since start, or 0 if not started."""
        start = self._start.get(chat)
        if start is None:
            return 0.0
        return time.time() - start

    def format_timer_line(self, chat: str = "default") -> str:
        """Build the timer display line."""
        elapsed = self.get_elapsed(chat)
        formatted = _fmt(elapsed)
        return f"\n*⏱ Answered in {formatted}*\n"
