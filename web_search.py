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

    async def read_page(self, url: str) -> str:
        """Fetch and extract readable text from a single page via the gateway."""
        result = await self._get("/read", {"url": url})
        if "error" in result:
            return UNAVAILABLE.format(why=result["error"])
        title = result.get("title", "")
        text = result.get("text", "")
        parts = []
        if title:
            parts.append(f"# {title}\n")
        parts.append(text)
        return "\n".join(parts).strip()
