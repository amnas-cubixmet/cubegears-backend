"""Single DB_* environment contract tests; no live database or broker required."""
import os
import subprocess
import sys
import unittest
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent


class ProductionEnvironmentTests(unittest.TestCase):
    def env(self, **updates):
        value = dict(os.environ)
        for key in (
            "DATABASE_URL", "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB",
            "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_SSLMODE",
            "POSTGRES_CONNECT_TIMEOUT_SECONDS", "SQLITE_DATABASE_PATH",
            "ALLOW_LEGACY_PRODUCTION_SQLITE", "DB_ENGINE", "DB_NAME",
            "DB_USER", "DB_PASSWORD", "DB_HOST", "DB_PORT", "DB_CONN_MAX_AGE",
            "VERCEL", "CELERY_BROKER_URL", "SERVICE_ROLE", "DJANGO_DEBUG",
        ):
            value.pop(key, None)
        value.update({
            "DJANGO_DEBUG": "False",
            "DJANGO_SECRET_KEY": "test-only-strong-django-signing-key-not-for-deploy-000000",
            "JWT_SIGNING_KEY": "test-only-strong-jwt-signing-key-not-for-deploy-000000",
            "DJANGO_ALLOWED_HOSTS": "test.example.com",
            "SERVICE_ROLE": "web",
        })
        value.update(updates)
        return value

    def probe(self, expression="settings.DATABASES['default']['ENGINE']", **updates):
        return subprocess.run(
            [sys.executable, "-c", f"from django.conf import settings; print({expression})"],
            env=self.env(**updates), cwd=ROOT, capture_output=True,
            text=True, timeout=20, check=False,
        )

    def postgres(self, **updates):
        fields = {
            "DB_ENGINE": "django.db.backends.postgresql",
            "DB_NAME": "cubixgear_db",
            "DB_USER": "cubixgear_user",
            "DB_PASSWORD": "unit-test-only-not-a-real-secret",
            "DB_HOST": "localhost",
            "DB_PORT": "5432",
            "DB_CONN_MAX_AGE": "0",
        }
        fields.update(updates)
        return fields

    def test_local_sqlite_requires_explicit_engine(self):
        result = self.probe(DJANGO_DEBUG="True", DB_ENGINE="django.db.backends.sqlite3", DB_NAME="db.sqlite3")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("django.db.backends.sqlite3", result.stdout)

    def test_existing_local_sqlite_path_is_preserved(self):
        result = self.probe(
            expression="settings.DATABASES['default']['NAME'].name",
            DJANGO_DEBUG="True",
            SQLITE_DATABASE_PATH="workshop-existing.sqlite3",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("workshop-existing.sqlite3", result.stdout)

    def test_conflicting_local_sqlite_paths_fail_closed(self):
        result = self.probe(
            DJANGO_DEBUG="True", DB_ENGINE="django.db.backends.sqlite3",
            DB_NAME="new.sqlite3", SQLITE_DATABASE_PATH="existing.sqlite3",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("point to different SQLite databases", result.stderr)

    def test_missing_db_engine_fails_closed(self):
        result = self.probe(DJANGO_DEBUG="True")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DB_ENGINE must be set", result.stderr)

    def test_production_cannot_use_sqlite(self):
        result = self.probe(DB_ENGINE="django.db.backends.sqlite3", DB_NAME="db.sqlite3")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Production requires DB_ENGINE", result.stderr)

    def test_db_fields_select_postgres_and_zero_connection_age(self):
        fields = self.postgres()
        result = self.probe(
            expression="(settings.DATABASES['default']['ENGINE'], settings.DATABASES['default']['CONN_MAX_AGE'])",
            **fields,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("django.db.backends.postgresql", result.stdout)
        self.assertIn(", 0)", result.stdout)

    def test_db_fields_missing_credentials_fail_closed(self):
        result = self.probe(DB_ENGINE="django.db.backends.postgresql", DB_NAME="cubixgear_db")
        self.assertNotEqual(result.returncode, 0)
        for field in ("DB_USER", "DB_PASSWORD", "DB_HOST", "DB_PORT"):
            self.assertIn(field, result.stderr)

    def test_invalid_port_and_connection_age_fail(self):
        for changes, expected in (
            ({"DB_PORT": "65536"}, "DB_PORT"),
            ({"DB_CONN_MAX_AGE": "-1"}, "DB_CONN_MAX_AGE"),
            ({"DB_CONN_MAX_AGE": "garbage"}, "DB_CONN_MAX_AGE"),
        ):
            with self.subTest(changes=changes):
                result = self.probe(**self.postgres(**changes))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)

    def test_invalid_engine_fails(self):
        result = self.probe(**self.postgres(DB_ENGINE="django.db.backends.mysql"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DB_ENGINE must be", result.stderr)

    def test_old_connection_formats_are_rejected(self):
        for obsolete in (
            {"POSTGRES_HOST": "localhost"},
            {"DATABASE_URL": "postgresql://legacy:legacy@localhost/legacy"},
            {"SQLITE_DATABASE_PATH": "db.sqlite3"},
            {"ALLOW_LEGACY_PRODUCTION_SQLITE": "True"},
        ):
            with self.subTest(obsolete=tuple(obsolete)):
                result = self.probe(**self.postgres(**obsolete))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Remove old database environment variables", result.stderr)

    def test_external_production_connection_requires_tls(self):
        result = self.probe(
            expression="settings.DATABASES['default']['OPTIONS']['sslmode']",
            **self.postgres(DB_HOST="db.example.com"),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("require", result.stdout)

    def test_worker_requires_broker(self):
        result = self.probe(SERVICE_ROLE="worker", **self.postgres())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Separate Celery workers", result.stderr)

    def test_committed_templates_contain_no_secrets(self):
        local = dotenv_values(ROOT / ".env.example")
        prod = dotenv_values(ROOT / ".env.production.example")
        for values, expected_engine in (
            (local, "django.db.backends.sqlite3"),
            (prod, "django.db.backends.postgresql"),
        ):
            for key in (
                "DB_PASSWORD", "EMAIL_HOST_PASSWORD", "RAZORPAY_KEY_SECRET",
                "RAZORPAY_WEBHOOK_SECRET", "WHATSAPP_PLATFORM_ACCESS_TOKEN",
            ):
                self.assertFalse(values.get(key), key)
            self.assertEqual(values.get("DB_ENGINE"), expected_engine)
            self.assertEqual(values.get("DB_CONN_MAX_AGE"), "0")
        self.assertFalse(prod.get("DJANGO_SECRET_KEY"))
        self.assertFalse(prod.get("JWT_SIGNING_KEY"))


if __name__ == "__main__":
    unittest.main()
