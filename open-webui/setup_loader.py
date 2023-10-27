"""
install_loader.py: puts open-webui/loader.js (voice fixes for every browser) where Open WebUI serves it, as
/static/loader.js. Run with Open WebUI's own Python before it starts (services/open-webui.sh on the main PC,
windows/run-service.ps1 on the helper PC), and by ./localllm.sh deploy when loader.js changes.

Open WebUI copies its frontend's static files, an empty loader.js among them, into its static folder on every start,
so the file goes into both: the frontend's copy (what the next start uses) and the static folder (what a running
Open WebUI serves now; a reload of the page picks it up). An Open WebUI update brings back the empty file; the next

start of Open WebUI (or ./localllm.sh deploy) puts this one back.
"""

import importlib.util
import os
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parent / "loader.js"


def targets() -> list[Path]:
    """Return the paths where loader.js should be placed."""
    webui_root = None
    for candidate in [
        Path(os.environ.get("OPEN_WEBUI_DIR", "")),
        Path.home() / "open-webui",
        Path(__file__).resolve().parent.parent,
    ]:
        if candidate and (candidate / "app" / "web").exists():
            webui_root = candidate
            break

    if not webui_root:
        print("Could not find Open WebUI root. Set OPEN_WEBUI_DIR or run from open-webui/.", file=sys.stderr)
        return []

    static_dir = webui_root / "static"
    frontend_static = webui_root / "app" / "web" / "static"

    paths = [static_dir / "loader.js"]
    if frontend_static.exists():
        paths.append(frontend_static / "loader.js")

    return paths


def main() -> int:
    """Copy loader.js to every target path."""
    if not SOURCE.exists():
        print(f"loader.js not found at {SOURCE}", file=sys.stderr)
        return 1

    target_paths = targets()
    if not target_paths:
        return 1

    content = SOURCE.read_text(encoding="utf-8")
    for target in target_paths:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        print(f"Installed loader.js to {target}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
