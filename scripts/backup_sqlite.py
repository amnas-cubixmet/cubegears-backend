#!/usr/bin/env python3
"""Create a consistent SQLite backup without altering the source database.

Stop old Django/Celery writers for final cutover. Output must be outside Git.
Example:
  python scripts/backup_sqlite.py --source db.sqlite3 --output db-backups/pre-postgres.sqlite3
"""
import argparse
import os
import sqlite3
import sys
from pathlib import Path


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    source = args.source.resolve()
    output = args.output.resolve()

    if not source.is_file():
        parser.error("Source SQLite database does not exist.")
    if output.exists():
        parser.error("Backup destination already exists; refusing to overwrite.")
    if source == output:
        parser.error("Source and destination must differ.")
    if output.suffix.lower() not in (".sqlite3", ".db", ".sqlite"):
        parser.error("Use a .sqlite3, .db or .sqlite backup extension.")
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        # URI mode=ro prevents accidentally creating the source database.
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=30) as src:
            with sqlite3.connect(output, timeout=30) as dst:
                src.backup(dst)
                result = dst.execute("PRAGMA integrity_check").fetchone()
                if not result or result[0].lower() != "ok":
                    raise RuntimeError("Backup integrity check failed.")
        if os.name == "posix":
            output.chmod(0o600)
    except Exception:
        # Do not keep partial/corrupt backups.
        try:
            output.unlink(missing_ok=True)
        except OSError:
            pass
        print("SQLite backup failed; original file was not modified.", file=sys.stderr)
        return 1

    print("SQLite backup completed and integrity-checked:", output)
    print("Keep this backup encrypted and outside the repository; verify restore in staging.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
