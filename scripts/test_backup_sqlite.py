"""Backup regression tests: restore-safe snapshot without source mutations."""
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().with_name("backup_sqlite.py")


class SQLiteBackupTests(unittest.TestCase):
    def test_backup_preserves_data_and_rejects_overwriting_existing_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            old = folder / "old.sqlite3"
            backup = folder / "backups" / "pre-postgres.sqlite3"
            with sqlite3.connect(old) as db:
                db.execute("CREATE TABLE transactions (id INTEGER PRIMARY KEY, amount TEXT)")
                db.execute("INSERT INTO transactions(amount) VALUES ('1275.50')")
            args = [sys.executable, str(SCRIPT), "--source", str(old), "--output", str(backup)]
            first = subprocess.run(args, text=True, capture_output=True, timeout=20)
            self.assertEqual(first.returncode, 0, first.stderr)
            with sqlite3.connect(backup) as db:
                self.assertEqual(db.execute("SELECT amount FROM transactions").fetchone()[0], "1275.50")
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            second = subprocess.run(args, text=True, capture_output=True, timeout=20)
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("refusing to overwrite", second.stderr)
            with sqlite3.connect(old) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
