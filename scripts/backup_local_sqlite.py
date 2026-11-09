"""Back up the actual configured local SQLite DB before Django migrations.

No production credentials or settings are rewritten. PostgreSQL is untouched.
Backups stay under the gitignored backend/db-backups directory.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")


def main() -> int:
    import django
    django.setup()
    from django.conf import settings

    database = settings.DATABASES["default"]
    if database["ENGINE"] != "django.db.backends.sqlite3":
        print("[backup] Configured DB is not SQLite; no file backup is needed.")
        return 0

    db_name = str(database["NAME"])
    if db_name == ":memory:":
        print("[backup] In-memory SQLite; skipping.")
        return 0

    sqlite_path = Path(db_name).expanduser()
    if not sqlite_path.is_absolute():
        sqlite_path = ROOT / sqlite_path
    sqlite_path = sqlite_path.resolve()
    if not sqlite_path.is_file():
        print("[backup] SQLite DB file does not exist yet; migration will create it.")
        return 0

    backup_dir = ROOT / "db-backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / f"{sqlite_path.stem}-{date.today():%Y%m%d}.sqlite3"
    if destination.exists():
        print(f"[backup] Today's SQLite backup already exists: {destination.name}")
        return 0

    # SQLite's own backup API creates a coherent snapshot, even with readers.
    # A read-only source also avoids accidentally creating an empty DB.
    try:
        with sqlite3.connect(f"file:{sqlite_path.as_posix()}?mode=ro", uri=True) as source:
            with sqlite3.connect(destination) as target:
                source.backup(target)
    except (sqlite3.Error, OSError):
        destination.unlink(missing_ok=True)
        raise

    print(f"[backup] Saved SQLite backup: {destination.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
