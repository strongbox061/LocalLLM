"""
search_gateway_migrate.py: moves a setup from before the search gateway had its own settings and database
(run by install/search-gateway.sh with the gateway's Python; safe to run again: what's done already is skipped).

  1. Settings: when ~/.config/localllm/search-gateway.json doesn't exist yet, the old Open WebUI tool's Valves (API keys,
     order, limits) are read from Open WebUI's database (read-only) and written there, only you can read it. Open
     WebUI's copy is removed later by install/open-webui-config.sh (open-webui/sync.py), once this file exists.
  2. State: the usage counts, result cache and search log that the tool kept next to Open WebUI's database
     (search_gateway_*.json/.jsonl, or web_research_* before the rename) go into the gateway's database when it has
     no usage counts yet; the old files are then deleted.

Never prints a setting's value. Usage: <gateway python> search_gateway_migrate.py [webui.db]
"""

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SETTINGS_FILE = Path(os.path.expanduser(os.environ.get("SEARCH_GATEWAY_SETTINGS", "~/.config/localllm/search-gateway.json")))
OLD_TOOL_IDS = ("search_gateway", "local_web_research")
OLD_NAMES = ("search_gateway", "web_research")  # file name prefixes, newest first
DROPPED = {"usage_file", "cache_file"}  # settings the engine no longer has


def engine():
    spec = importlib.util.spec_from_file_location("search_gateway_engine", HERE / "search_gateway_engine.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def old_valves(webui_db: Path) -> dict:
    """The old tool's Valves from Open WebUI's database, or {} (no database, no tool, or a thin client already)."""
    if not webui_db.exists():
        return {}
    import sqlite3
    con = sqlite3.connect(f"file:{webui_db}?mode=ro", uri=True, timeout=10)
    try:
        for tool_id in OLD_TOOL_IDS:
            row = con.execute("SELECT valves FROM tool WHERE id = ?", (tool_id,)).fetchone()
            try:
                valves = json.loads(row[0]) if row and row[0] else {}
            except ValueError:
                valves = {}
            if isinstance(valves, dict) and ("service_order" in valves or any(k.endswith("_api_key") for k in valves)):
                return valves
    finally:
        con.close()
    return {}


def write_settings(data: dict):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=SETTINGS_FILE.parent, prefix=".search-gateway.", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, SETTINGS_FILE)


def migrate_settings(eng, webui_db: Path):
    if SETTINGS_FILE.exists():
        print(f"  same     settings ({SETTINGS_FILE})")
        return
    valves = old_valves(webui_db)
    if not valves:
        print(f"  none     no old settings in Open WebUI; set your API keys on the settings page (/settings)")
        return
    fields = eng.Tools.Valves.model_fields
    known = {k: v for k, v in valves.items() if k in fields and k not in DROPPED}
    checked = eng.Tools.Valves(**known)  # fails loudly rather than writing settings the engine can't use
    out = {k: getattr(checked, k) for k in fields if getattr(checked, k) != fields[k].default}
    write_settings(out)
    keys = sum(1 for k in out if k.endswith("_api_key"))
    print(f"  moved    settings from Open WebUI: {len(out)} changed from the defaults, {keys} API keys -> {SETTINGS_FILE}")


def migrate_state(eng, data_dir: Path):
    def first(suffix):
        for prefix in OLD_NAMES:
            p = data_dir / f"{prefix}_{suffix}"
            if p.exists():
                return p
        return None

    usage_file, cache_file = first("usage.json"), first("cache.json")
    logs = [p for prefix in OLD_NAMES for p in (data_dir / f"{prefix}_log.jsonl.1", data_dir / f"{prefix}_log.jsonl") if p.exists()]
    if not (usage_file or cache_file or logs):
        print("  same     state (nothing left next to Open WebUI's database)")
        return
    con = eng._db()
    try:
        has_usage = con.execute("SELECT 1 FROM doc WHERE name = 'usage'").fetchone() is not None
        con.execute("BEGIN IMMEDIATE")
        moved = []
        if usage_file and not has_usage:
            try:
                usage = json.loads(usage_file.read_text())
            except ValueError:
                usage = None
            if isinstance(usage, dict):
                con.execute("INSERT INTO doc (name, data) VALUES ('usage', ?)", (json.dumps(usage),))
                moved.append("usage counts")
        if cache_file:
            try:
                cache = json.loads(cache_file.read_text())
            except ValueError:
                cache = {}
            n = 0
            for key, entry in (cache.items() if isinstance(cache, dict) else []):
                if isinstance(entry, dict) and isinstance(entry.get("t"), (int, float)):
                    con.execute("INSERT OR IGNORE INTO cache (key, t, provider, results) VALUES (?, ?, ?, ?)",
                                (key, entry["t"], str(entry.get("provider") or ""), json.dumps(entry.get("results") or [])))
                    n += 1
            moved.append(f"{n} cached searches")
        n = 0
        for path in logs:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict) and isinstance(entry.get("t"), (int, float)):
                    con.execute("INSERT INTO log (t, entry) VALUES (?, ?)", (entry["t"], json.dumps(entry, ensure_ascii=False)))
                    n += 1
        if logs:
            moved.append(f"{n} logged searches")
        con.execute("COMMIT")
    finally:
        if con.in_transaction:
            con.execute("ROLLBACK")
        con.close()
    # In the database now (or older copies of what it has): the old files and their lock files go.
    for prefix in OLD_NAMES:
        for suffix in ("usage.json", "cache.json", "log.jsonl", "log.jsonl.1"):
            for p in (data_dir / f"{prefix}_{suffix}", data_dir / f"{prefix}_{suffix}.lock"):
                if p.exists():
                    p.unlink()
    print(f"  moved    {', '.join(moved) or 'nothing usable'} -> {eng.STATE_DB} (old files deleted)"
          + ("; usage counts kept as they were (the database had them already)" if has_usage and usage_file else ""))


def main() -> int:
    webui_db = Path(os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "~/.open-webui/webui.db"))
    eng = engine()
    migrate_settings(eng, webui_db)
    migrate_state(eng, webui_db.parent)
    return 0


if __name__ == "__main__":
    sys.exit(main())
