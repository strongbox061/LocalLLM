"""
install_loader.py: puts open-webui/loader.js (voice fixes for every browser) where Open WebUI serves it, as
/static/loader.js. Run with Open WebUI's own Python before it starts (services/open-webui.sh on the main PC,
windows/run-service.ps1 on the helper PC), and by ./localllm.sh deploy when loader.js changes.

Open WebUI copies its frontend's static files, an empty loader.js among them, into its static folder on every start,
so the file goes into both: the frontend's copy (what the next start uses) and the static folder (what a running
Open WebUI serves now; a reload of the page picks it up). An Open WebUI update brings back the empty file; the next
start puts this one back. Finds the package without importing it (importing Open WebUI starts its whole setup).
Safe to run again: prints only when it changed something.

Usage: python install_loader.py
"""

import importlib.util
import os
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parent / "loader.js"


def targets() -> list[Path]:
    spec = importlib.util.find_spec("open_webui")
    if not spec or not spec.submodule_search_locations:
        sys.exit("install_loader.py: Open WebUI isn't installed for this Python.")
    pkg = Path(next(iter(spec.submodule_search_locations)))
    front = Path(os.environ.get("FRONTEND_BUILD_DIR") or pkg / "frontend") / "static"
    static = Path(os.environ.get("STATIC_DIR") or pkg / "static")
    return [p / "loader.js" for p in (front, static) if p.is_dir()]


def main() -> int:
    data = SOURCE.read_bytes()
    found = targets()
    if not found:
        print("install_loader.py: Open WebUI's static folders weren't found.", file=sys.stderr)
        return 1
    for t in found:
        if t.is_file() and t.read_bytes() == data:
            continue
        # A new file in its place, not a write into it: uv hard-links installed files to its cache, which a write
        # would change for every other install too.
        tmp = t.with_name(t.name + ".new")
        tmp.write_bytes(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, t)
        print(f"Open WebUI's voice fixes (loader.js) installed in {t}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
