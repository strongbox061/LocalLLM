#!/usr/bin/env python3
"""Early SearXNG + Brave Search stdlib HTTP proxy prototype."""

import json
import os
import urllib.error
import urllib.parse
import urllib.request


SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://localhost:8080")
BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "")


def searxng_search(query, engines=None):
    params = {"q": query, "format": "json"}
    if engines:
        params["engines"] = ",".join(engines)
    url = f"{SEARXNG_URL}/search?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"SearXNG request failed: {exc.code} {exc.reason}") from exc


def brave_search(query):
    if not BRAVE_API_KEY:
        raise RuntimeError("BRAVE_API_KEY is not set")
    url = (
        f"https://api.search.brave.com/res/v1/web/search"
        f"?q={urllib.parse.quote(query)}"
    )
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {BRAVE_API_KEY}")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Brave search failed: {exc.code} {exc.reason}") from exc


def _extract_results(data, max_results=10):
    results = data.get("results", [])
    if not isinstance(results, list):
        return []
    return [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")} for r in results[:max_results]]


def search(query, source="searxng", engines=None):
    if source == "brave":
        return brave_search(query)
    return searxng_search(query, engines)


if __name__ == "__main__":
    import sys
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "python llm"
    results = _extract_results(search(query))
    for r in results:
        print(f"{r['title']}\n  {r['url']}\n  {r['snippet']}\n")
