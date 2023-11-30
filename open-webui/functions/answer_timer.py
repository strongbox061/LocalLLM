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
    """Remove the timer line from content before sending to the model."""
    return TIMER_LINE_RE.sub("", content)


class Filter(BaseModel):
    """Timer filter for Open WebUI completions."""

    enabled: bool = Field(default=True)
    start_time: float | None = Field(default=None)

    def set_start(self):
        """Record when the request started."""
        self.start_time = time.time()

    def get_elapsed(self) -> float:
        """Return elapsed seconds since start, or 0 if not started."""
        if self.start_time is None:
            return 0.0
        return time.time() - self.start_time

    def format_timer_line(self) -> str:
        """Build the timer display line."""
        elapsed = self.get_elapsed()
        formatted = _fmt(elapsed)
        return f"\n*⏱ Answered in {formatted}*\n"
