"""
title: Search Gateway
author: LocalLLM
version: 1.1.0
description: Web search and page reading through the local search gateway (services/search_gateway.py, the search-gateway service on 127.0.0.1:8090), which Pi uses too. The gateway holds the search services, their API keys and order (its /settings), and caches results in SQLite (its /cache). This tool connects to it for both query-based search and raw page fetching.
"""

import aiohttp
from pydantic import BaseModel, Field

UNAVAILABLE = (
    "Web search is not working right now (the search gateway isn't answering: {why}). Stop searching. If you have no "
    "sources, tell the user that web search is unavailable and that the search-gateway service must be started "
    "(./start.sh). Do NOT give prices, rates or other current figures from memory."
)


class Tools:
    class Valves(BaseModel):
        gateway_url: str = Field(
            default="http://127.0.0.1:8090",
            description="The search gateway (install/search-gateway.sh). Its settings page: <this address>/settings.",
        )
        timeout_seconds: int = Field(default=300, description="Longest wait for one search or page read.")

    def __init__(self):
        self.valves = self.Valves()
        self.citation = False  # the gateway's citations are passed on instead

    @staticmethod
    def _last_user_text(messages) -> str:
        for m in reversed(messages or []):
            if isinstance(m, dict) and m.get("role") == "user":
                content = m.get("content")
                if isinstance(content, list):
                    return " ".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
                return str(content or "")
        return ""

    async def _call(self, method: str, args: dict, metadata, emitter, user_text: str = "", first_status: str = "") -> str:
        meta = {k: str((metadata or {}).get(k)) for k in ("message_id", "chat_id") if (metadata or {}).get(k)}
        body = {"args": args, "metadata": meta, "user_text": user_text}
        url = f"{self.valves.gateway_url.rstrip('/')}/tool/{method}"
        try:
            timeout = aiohttp.ClientTimeout(total=self.valves.timeout_seconds)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=body) as r:
                    data = await r.json(content_type=None)
                    if r.status != 200:
                        why = (data or {}).get("error") if isinstance(data, dict) else ""
                        return UNAVAILABLE.format(why=f"HTTP {r.status}{f', {why}' if why else ''}")
        except Exception as e:
            if emitter:
                await emitter({"type": "status", "data": {"description": "Search gateway not answering", "done": True}})
            return UNAVAILABLE.format(why=type(e).__name__)
        if emitter:
            shown = first_status
            for event in data.get("events") or []:
                if not isinstance(event, dict) or event.get("type") not in ("status", "citation"):
                    continue
                if event["type"] == "status":
                    text = (event.get("data") or {}).get("description")
                    if text == shown:  # already shown while waiting for the gateway
                        continue
                    shown = text
                await emitter(event)
        return str(data.get("result") or "")

    async def _get(self, path: str, params: dict | None = None) -> dict:
        """Send a GET request to the search gateway and return parsed JSON."""
        url = f"{self.valves.gateway_url.rstrip('/')}/{path.lstrip('/')}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=self.valves.timeout_seconds)) as resp:
                    resp.raise_for_status()
                    return await resp.json()
        except Exception as e:
            return {"error": str(e)}

    async def search(self, query: str) -> str:
        """Run a web search through the gateway and return formatted results."""
        result = await self._get("/search", {"q": query})
        if "error" in result:
            return UNAVAILABLE.format(why=result["error"])
        snippets = result.get("results", [])
        if not snippets:
            return f"No results found for: {query}"
        lines = []
        for i, r in enumerate(snippets, 1):
            title = r.get("title", "Untitled")
            url = r.get("url", "")
            snippet = r.get("snippet", "")
            lines.append(f"{i}. {title}\n   {snippet}\n   {url}")
        return "\n\n".join(lines)

    async def read_page(
        self,
        url: str,
        part: int = 1,
        __metadata__: dict = None,
        __event_emitter__=None,
    ) -> str:
        """
        Open a web page and return its text.
        Only open URLs that appeared in search results or that the user gave you; don't make up URLs.
        After reading, note the key facts you take from it in your answer.
        If the page is blocked or empty, don't retry that site; use another source.
        A long page comes in parts; the note at its end lists the page's sections. To read on, call again with part=2, 3...
        To start at a section, add its #anchor to the URL (e.g. https://example.com/page#install).
        :param url: The full URL of the page (starting with http:// or https://), optionally with a #section.
        :param part: Which part of a long page: 1 (the start, or the #section's start), 2, 3...
        """
        status = f"Reading: {url}" + (f" (part {part})" if part and part > 1 else "")
        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": status, "done": False}})
        return await self._call("read_page", {"url": url, "part": part}, __metadata__, __event_emitter__,
                                first_status=status)
