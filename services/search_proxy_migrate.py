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
    """Move usage counts, cache and search log into the gateway database."""
    db_path = eng.settings().get("cache_db", str(data_dir / "cache.db"))

    # Check if the gateway already has usage counts; if so, skip.
    try:
        conn = __import__("sqlite3").connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM usage").fetchone()[0]
        conn.close()
        if count > 0:
            print("usage counts already present, skipping state migration")
            return
    except (sqlite3.OperationalError, FileNotFoundError):
        pass

    # Find old state files in the data directory next to Open WebUI's database.
    old_files = []
    for prefix in OLD_NAMES:
        for suffix in (".json", ".jsonl"):
            f = data_dir / f"{prefix}{suffix}"
            if f.exists():
                old_files.append(f)

    if not old_files:
        print("no old state files found, nothing to migrate")
        return

    # Import the engine's state module to load old records.
    state_mod = importlib.util.import_module("search_gateway.state")
    for f in old_files:
        state_mod.migrate_file(str(f), db_path)
        f.unlink()
        print(f"migrated and removed {f.name}")


def main() -> int:
    webui_db = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if webui_db is None:
        # Look for Open WebUI's database in common locations.
        candidates = [
            Path(os.environ.get("OPEN_WEBUI_DATA", "~/.local/share/open-webui")),
            Path.home() / "open-webui" / "data",
        ]
        for c in candidates:
            candidate = c.expanduser()
            if candidate.exists():
                db = candidate / "app.db"
                if db.exists():
                    webui_db = db
                    break

    if webui_db is None or not webui_db.exists():
        print("error: Open WebUI database not found; pass path as argument", file=sys.stderr)
        return 1

    eng = engine()
    data_dir = webui_db.parent

    migrate_settings(eng, webui_db)
    migrate_state(eng, data_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
