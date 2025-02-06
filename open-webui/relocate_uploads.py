"""
Points Open WebUI's uploaded files at the uploads folder of the PC its data is on now (README, "Two-PC setup").

Open WebUI keeps each upload's full path in its database (file.path), e.g. /home/<you>/.open-webui/uploads/<id>_x.pdf
on Linux or D:\\...\\uploads\\<id>_x.pdf on Windows. After ./localllm.sh switch moves the data, those paths name the
other PC, and opening or re-reading an attachment fails. This rewrites each one to <uploads folder>/<its file name>,
only when that file is there; it refuses (changes nothing) if any upload is missing. Run by the switch while Open
WebUI is stopped.

Usage: python relocate_uploads.py <webui.db> <uploads folder>
"""

import os
import sqlite3
import sys


def main(argv):
    if len(argv) != 3:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    db, uploads = argv[1], os.path.abspath(argv[2])
    con = sqlite3.connect(db)
    try:
        rows = con.execute("SELECT id, path FROM file WHERE path IS NOT NULL AND path != ''").fetchall()
        changes, missing = [], []
        for file_id, path in rows:
            name = path.replace("\\", "/").rsplit("/", 1)[-1]
            new = os.path.join(uploads, name)
            if not os.path.isfile(new):
                missing.append(name)
            elif new != path:
                changes.append((new, file_id))
        if missing:
            print(f"ERROR: {len(missing)} upload(s) missing in {uploads}: {', '.join(missing[:5])}", file=sys.stderr)
            return 1
        with con:
            con.executemany("UPDATE file SET path = ? WHERE id = ?", changes)
        print(f"  uploads: {len(changes)} path(s) now in {uploads}, {len(rows) - len(changes)} already there")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
