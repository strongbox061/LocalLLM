"""
title: Old Pages
author: LocalLLM
version: 1.1.0
description: Once a chat gets long, the text of web pages read for earlier answers is replaced by a short note with the page's address before the chat goes to the model. The answer being written keeps its pages in full, your messages and the answers stay as they are, and the model can read a page again when you ask about something its answer left out. Without it, every page is sent again with each follow-up.
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


def _text(content):
    """Extract plain text from a message content field."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
        return "\n".join(parts)
    return str(content)


def _chars(message: dict) -> int:
    """Count total characters in a message."""
    return len(_text(message.get("content", "")))


def _is_prompt(message: dict) -> bool:
    """Check if a message is from the user (a prompt)."""
    role = message.get("role", "")
    return role == "user"


class Filter(BaseModel):
    """Filter that trims old web page content from chat history."""

    max_chars: int = Field(default=100000, description="Max characters for the full message list")
    note_text: str = Field(default=NOTE, description="Replacement note for trimmed pages")

    def apply(self, messages: list) -> list:
        """Trim page content from old user messages that exceed max_chars."""
        total = sum(_chars(m) for m in messages)

        if total <= self.max_chars:
            return messages

        result = []
        trimmed = False

        for msg in messages:
            if _is_prompt(msg) and not trimmed:
                content = _text(msg.get("content", ""))
                new_content = self._trim_page_text(content)
                if new_content != content:
                    trimmed = True
                if isinstance(msg.get("content"), list):
                    msg = {**msg, "content": [{"type": "text", "text": new_content}]}
                else:
                    msg = {**msg, "content": new_content}
            result.append(msg)

        return result

    def _trim_page_text(self, text: str) -> str:
        """Replace full page content with a short note."""
        if "[Page text removed to save space:" not in text:
            return text

        lines = text.split("\n")
        new_lines = []
        skip = False

        for line in lines:
            if line.startswith(NOTE_START):
                skip = True
                url = line[len(NOTE_START):].strip().rstrip("]")
                new_lines.append(f"{NOTE_START} {url}]")
            elif skip and line.strip() == "]":
                skip = False
            elif not skip:
                new_lines.append(line)

        return "\n".join(new_lines)
