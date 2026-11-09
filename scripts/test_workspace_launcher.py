"""Regression checks for the Windows full-workspace launcher and local DB safety."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
from django.conf import settings

import backup_local_sqlite
import workspace_preflight

ROOT = Path(__file__).resolve().parent.parent


class WorkspaceBatchContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launcher = (ROOT / "start-workspace.bat").read_text(encoding="utf-8").lower()

    def test_migrations_run_after_preflight_and_backup_but_before_services(self):
        script = self.launcher
        self.assertLess(script.index("scripts\\workspace_preflight.py"),
                        script.index("scripts\\backup_local_sqlite.py"))
        self.assertLess(script.index("scripts\\backup_local_sqlite.py"),
                        script.index("manage.py migrate --noinput"))
        self.assertLess(script.index("manage.py migrate --noinput"),
                        script.index('start "cubixgear backend'))
        self.assertNotIn("manage.py makemigrations", script)
        self.assertNotIn("flush --noinput", script)

    def test_frontends_use_separate_npm_terminals_and_backend_existing_supervisor(self):
        script = self.launcher
        for expected in (
            "venv\\scripts\\python.exe", ".venv\\scripts\\python.exe",
            "call start-all.bat", 'start "cubixgear company panel',
            'start "cubixgear public website', 'start "cubixgear customer website',
            "npm run dev", "npm.cmd ci", "npm.cmd install",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, script)

    def test_private_env_and_failed_migrations_block_startup(self):
        script = self.launcher
        self.assertIn('if not exist "%backend%.env"', script)
        self.assertIn("migration failed. services were not started.", script)
        self.assertIn("if errorlevel 1 goto failed", script)
        self.assertIn("scripts\\workspace_preflight.py", script)


class LocalEnvironmentSafetyTests(unittest.TestCase):
    def test_production_debug_false_is_rejected(self):
        with patch("dotenv.load_dotenv"), patch.object(settings, "DEBUG", False):
            self.assertEqual(workspace_preflight.main(), 1)

    def test_remote_postgresql_is_rejected(self):
        with (
            patch("dotenv.load_dotenv"),
            patch.object(settings, "DEBUG", True),
            patch.object(settings, "SECURE_SSL_REDIRECT", False, create=True),
            patch.object(settings, "DATABASES", {
                "default": {"ENGINE": "django.db.backends.postgresql", "HOST": "remote-prod.example"}
            }),
        ):
            self.assertEqual(workspace_preflight.main(), 1)

    def test_remote_redis_is_rejected(self):
        with (
            patch("dotenv.load_dotenv"),
            patch.object(settings, "DEBUG", True),
            patch.object(settings, "SECURE_SSL_REDIRECT", False, create=True),
            patch.object(settings, "DATABASES", {
                "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
            }),
            patch.object(settings, "CELERY_BROKER_URL", "redis://redis.example:6379/0",
                         create=True),
        ):
            self.assertEqual(workspace_preflight.main(), 1)

    def test_configured_custom_sqlite_file_is_backed_up_without_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = root / "custom-workshop.sqlite3"
            with sqlite3.connect(database) as db:
                db.execute("CREATE TABLE notes (value TEXT)")
                db.execute("INSERT INTO notes VALUES ('kept')")
            with (
                patch.object(settings, "DATABASES", {
                    "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": database}
                }),
                patch.object(backup_local_sqlite, "ROOT", root),
            ):
                self.assertEqual(backup_local_sqlite.main(), 0)
                copies = list((root / "db-backups").glob("*.sqlite3"))
                self.assertEqual(len(copies), 1)
                with sqlite3.connect(copies[0]) as snapshot:
                    self.assertEqual(snapshot.execute("SELECT value FROM notes").fetchone()[0],
                                     "kept")
                self.assertEqual(backup_local_sqlite.main(), 0)
                self.assertEqual(len(list((root / "db-backups").glob("*.sqlite3"))), 1)


if __name__ == "__main__":
    unittest.main()
