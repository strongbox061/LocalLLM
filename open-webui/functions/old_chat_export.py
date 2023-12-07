"""Temporary Open WebUI chat export filter.

Filters exported chat messages by an optional date threshold so that only
recent conversations are included in a given export run.  Intended as a
lightweight, self-contained utility — not a permanent module.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any


def _parse_iso(date_str: str) -> datetime | None:
    try:
        return datetime.fromisoformat(date_str)
    except (ValueError, TypeError):
        return None


def filter_chats(
    messages: list[dict[str, Any]],
    cutoff: str | None = None,
    max_messages: int | None = None,
) -> list[dict[str, Any]]:
    """Return *messages* keeping only those on or after *cutoff*.

    *cutoff* is an ISO-8601 string (e.g. ``2023-11-01T00:00:00-05:00``).
    When omitted all messages are returned.  *max_messages* limits the
    final result to that many entries (applied after filtering).
    """
    if cutoff is None:
        filtered = list(messages)
    else:
        threshold = _parse_iso(cutoff)
        if threshold is None:
            print(f"warning: invalid cutoff '{cutoff}', returning all", file=sys.stderr)
            filtered = list(messages)
        else:
            filtered = [
                m for m in messages
                if _parse_iso(m.get("timestamp", ""))
                and _parse_iso(m["timestamp"]) >= threshold
            ]

    if max_messages is not None:
        filtered = filtered[:max_messages]
    return filtered


if __name__ == "__main__":
    raw = sys.stdin.read()
    data: dict[str, Any] = json.loads(raw)
    cutoff = data.get("cutoff")
    max_msgs = data.get("max_messages")
    messages = data.get("messages", [])
    result = filter_chats(messages, cutoff, max_msgs)
    print(json.dumps({"count": len(result), "messages": result}))
