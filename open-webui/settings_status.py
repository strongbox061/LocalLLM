"""
Prints, as JSON, the Open WebUI settings this repo sets (README, "Protected chats in Open WebUI"): the model
connection's addresses (never its keys), the embedding model, hybrid search, and each chat type preset's tools,
filters and web search. Only reads the database (opened read-only), so it is safe while Open WebUI runs. Used by tests/system-checks.sh: on this PC in single mode, on the
helper PC through windows/tasks.ps1 settings-status in split mode.

Usage: python settings_status.py <webui.db>
"""

import json
import sqlite3
import sys

CONFIG_KEYS = ("rag.embedding_engine", "rag.embedding_model", "rag.enable_hybrid_search", "openai.api_base_urls")


def main(argv):
    if len(argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    con = sqlite3.connect(f"file:{argv[1]}?mode=ro", uri=True, timeout=10)
    try:
        config = {k: json.loads(v) for k, v in con.execute(
            f"SELECT key, value FROM config WHERE key IN ({','.join('?' * len(CONFIG_KEYS))})", CONFIG_KEYS)}
        presets = {}
        for mid, meta in con.execute("SELECT id, meta FROM model WHERE id LIKE 'chat-%'"):
            meta = json.loads(meta or "{}")
            presets[mid[len("chat-"):]] = {
                "toolIds": meta.get("toolIds") or [],
                "filterIds": meta.get("filterIds") or [],
                "web_search": (meta.get("capabilities") or {}).get("web_search"),
            }
    finally:
        con.close()
    print(json.dumps({"config": config, "presets": presets}, sort_keys=True))
    return 0


if __name__ == "__main__":
    main(sys.argv)
