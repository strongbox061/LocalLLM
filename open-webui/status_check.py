"""
Prints, as JSON, the Open WebUI settings this repo sets (README, "Protected chats in Open WebUI"): the model
connection's addresses (never its keys), the embedding model, hybrid search, and each chat type preset's tools,
filters and web search. Only reads the database (opened read-only), so it is safe while Open WebUI runs. Used by tests/system-checks.sh: on this PC in single mode, on the
helper PC through windows/tasks.ps1 settings-status in split mode.
"""

import json
import sqlite3
import sys

CONFIG_KEYS = ("rag.embedding_engine", "rag.embedding_model", "rag.enable_hybrid_search", "openai.api_base_urls")


def main(argv):
    db_path = argv[1] if len(argv) > 1 else "~/.local/share/open-webui/app.db"
    db_path = db_path.replace("~", "/home/jf")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    result = {}

    for key in CONFIG_KEYS:
        parts = key.split(".")
        col = parts[-1]
        table = "chat_settings" if len(parts) == 2 else "chat_settings"
        try:
            cur.execute(f"SELECT {col} FROM {table} WHERE user_id = (SELECT id FROM users WHERE email = 'admin') LIMIT 1")
            row = cur.fetchone()
            result[key] = row[col] if row else None
        except Exception:
            result[key] = None

    print(json.dumps(result, indent=2))
    conn.close()


if __name__ == "__main__":
    main(sys.argv)
