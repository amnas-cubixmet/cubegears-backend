"""Safeguard the configured local SQLite DB before applying Django migrations.

Uses the existing backup_sqlite script for consistent, integrity-checked
snapshots. PostgreSQL is untouched. No database is deleted or overwritten.
"""
from __future__ import annotations

import hashlib
import os
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
    from backup_sqlite import run as backup_sqlite

    db = settings.DATABASES["default"]
    if db["ENGINE"] != "django.db.backends.sqlite3":
        print("[backup] PostgreSQL/other database: file snapshot not applicable.")
        return 0

    name = str(db["NAME"])
    if name == ":memory:":
        return 0
    path = Path(name).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    if not path.is_file():
        print("[backup] SQLite will be created by migrations; no existing data to back up.")
        return 0

    # Include a nonreversible path fingerprint to avoid collisions between
    # different SQLite databases with the same filename.
    identifier = hashlib.sha256(str(path).encode()).hexdigest()[:8]
    backup_dir = ROOT / "db-backups"
    dest = backup_dir / f"{path.stem}-{identifier}-{date.today():%Y%m%d}.sqlite3"
    if dest.exists():
        print(f"[backup] Today's snapshot is already present: {dest.name}")
        return 0

    return backup_sqlite(["--source", str(path), "--output", str(dest)])


if __name__ == "__main__":
    raise SystemExit(main())
