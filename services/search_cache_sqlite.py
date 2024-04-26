"""Minimal SQLite-backed cache for search results."""

import sqlite3
import json
from pathlib import Path
from typing import Optional


DB_PATH = Path(__file__).resolve().parent / "search_cache.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    conn = _connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS search_cache (
            query TEXT NOT NULL,
            engine TEXT NOT NULL,
            result_json TEXT NOT NULL,
            cached_at REAL DEFAULT (unixepoch())
        )
    """)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_query_engine ON search_cache(query, engine)"
    )
    conn.commit()
    conn.close()


def get(query: str, engine: str) -> Optional[dict]:
    init_db()
    conn = _connect()
    row = conn.execute(
        "SELECT result_json FROM search_cache WHERE query=? AND engine=?",
        (query, engine),
    ).fetchone()
    conn.close()
    return json.loads(row[0]) if row else None


def put(query: str, engine: str, result: dict) -> None:
    init_db()
    conn = _connect()
    conn.execute(
        "INSERT OR REPLACE INTO search_cache (query, engine, result_json) VALUES (?, ?, ?)",
        (query, engine, json.dumps(result)),
    )
    conn.commit()
    conn.close()


def delete(query: str, engine: str) -> bool:
    init_db()
    conn = _connect()
    cursor = conn.execute(
        "DELETE FROM search_cache WHERE query=? AND engine=?",
        (query, engine),
    )
    conn.commit()
    conn.close()
    return cursor.rowcount > 0


def clear() -> None:
    init_db()
    conn = _connect()
    conn.execute("DELETE FROM search_cache")
    conn.commit()
    conn.close()
