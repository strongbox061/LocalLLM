"""
Points Open WebUI's uploaded files at the uploads folder of the PC its data is on now (README, "Two-PC setup").

Open WebUI keeps each upload's full path in its database (file.path), e.g. /home/<you>/.open-webui/uploads/<id>_x.pdf
on Linux or D:\\...\\uploads\\<id>_x.pdf on Windows. After ./localllm.sh switch moves the data, those paths name the
other PC, and opening or re-reading an attachment fails. This rewrites each one to <uploads folder>/<its file name>,
only when that file is there; it refuses (changes nothing) if any upload is missing. Run by the switch while Open

WebUI is stopped so the database is not in use. Pass the full path to the new uploads root as the first argument.
"""

import os
import sqlite3
import sys


def main(argv):
    if len(argv) < 2:
        print("Usage: relocate_uploads.py <uploads-root>")
        sys.exit(1)

    uploads_root = argv[1]
    db_path = os.path.join(os.path.expanduser("~"), ".open-webui", "ollama.db")

    if not os.path.exists(db_path):
        print(f"Error: database not found at {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT id, file_name, file_path FROM files WHERE file_type = 'upload'")
    rows = cur.fetchall()

    if not rows:
        print("No uploads found in database.")
        conn.close()
        return

    missing = []
    updates = []

    for file_id, file_name, old_path in rows:
        new_path = os.path.join(uploads_root, file_name)
        if not os.path.exists(new_path):
            missing.append((file_id, file_name, new_path))
        else:
            updates.append((new_path, file_id))

    if missing:
        print("Refusing to update — missing files:")
        for file_id, file_name, expected in missing:
            print(f"  {file_name} (id={file_id}): expected {expected}")
        conn.close()
        sys.exit(1)

    for new_path, file_id in updates:
        cur.execute("UPDATE files SET file_path = ? WHERE id = ?", (new_path, file_id))

    conn.commit()
    print(f"Updated {len(updates)} upload path(s) to point to {uploads_root}")
    conn.close()


if __name__ == "__main__":
    main(sys.argv)
