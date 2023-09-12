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
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def brave_search(query):
    if not BRAVE_API_KEY:
        raise RuntimeError("BRAVE_API_KEY is not set")
    url = (
        f"https://api.brave.com/v1/search"
        f"?q={urllib.parse.quote(query)}"
    )
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {BRAVE_API_KEY}")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def search(query, source="searxng", engines=None):
    if source == "brave":
        return brave_search(query)
    return searxng_search(query, engines)


if __name__ == "__main__":
    import sys
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "python llm"
    result = search(query)
    print(json.dumps(result, indent=2))
