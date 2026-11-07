"""Environment selection tests; no live database, Redis or SMTP required."""
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
            "DB_ENGINE", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_HOST",
            "DB_PORT", "DB_CONN_MAX_AGE",
            "ALLOW_LEGACY_PRODUCTION_SQLITE", "VERCEL", "CELERY_BROKER_URL",
            "SERVICE_ROLE", "DJANGO_DEBUG", "SQLITE_DATABASE_PATH",
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

    def probe(self, **updates):
        return subprocess.run(
            [sys.executable, "-c",
             "from django.conf import settings; print(settings.DATABASES['default']['ENGINE'])"],
            env=self.env(**updates), cwd=ROOT, capture_output=True,
            text=True, timeout=20, check=False,
        )

    def test_local_sqlite_remains_supported(self):
        result = self.probe(DJANGO_DEBUG="True")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("django.db.backends.sqlite3", result.stdout)

    def test_production_cannot_silently_create_sqlite(self):
        result = self.probe()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Production needs PostgreSQL", result.stderr)

    def test_production_postgres_fields_selected_without_connection(self):
        result = self.probe(
            POSTGRES_HOST="postgres", POSTGRES_DB="cubegears",
            POSTGRES_USER="cubegears", POSTGRES_PASSWORD="test-only-not-real",
            POSTGRES_SSLMODE="disable",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("django.db.backends.postgresql", result.stdout)

    def test_db_prefixed_fields_select_postgresql_with_zero_connection_age(self):
        result = self.probe(
            DB_ENGINE="django.db.backends.postgresql",
            DB_NAME="cubixgear_db", DB_USER="cubixgear_user",
            DB_PASSWORD="unit-test-not-a-real-password",
            DB_HOST="localhost", DB_PORT="5432", DB_CONN_MAX_AGE="0",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("django.db.backends.postgresql", result.stdout)

    def test_db_prefix_missing_credentials_fail_closed(self):
        result = self.probe(DB_ENGINE="django.db.backends.postgresql", DB_HOST="localhost")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DB_NAME", result.stderr)
        self.assertIn("DB_USER", result.stderr)
        self.assertIn("DB_PASSWORD", result.stderr)

    def test_db_prefix_rejects_non_postgres_engine(self):
        result = self.probe(
            DB_ENGINE="django.db.backends.sqlite3",
            DB_HOST="localhost", DB_NAME="test", DB_USER="test", DB_PASSWORD="test",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DB_ENGINE must be", result.stderr)

    def test_db_prefix_rejects_mixed_connection_credentials(self):
        result = self.probe(
            DB_HOST="localhost", DB_NAME="test", DB_USER="test", DB_PASSWORD="test",
            POSTGRES_HOST="postgres",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Configure only one PostgreSQL connection format", result.stderr)

    def test_db_prefix_rejects_mixed_database_url(self):
        result = self.probe(
            DATABASE_URL="postgresql://test:test@localhost:5432/testdb",
            DB_HOST="localhost",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Configure only one PostgreSQL connection format", result.stderr)

    def test_production_database_url_selected_without_connection(self):
        result = self.probe(
            DATABASE_URL="postgresql://testuser:testpass@localhost:5432/testdb",
            POSTGRES_SSLMODE="require",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("django.db.backends.postgresql", result.stdout)

    def test_rejects_mixed_database_configuration(self):
        result = self.probe(
            DATABASE_URL="postgresql://test:test@localhost:5432/testdb",
            POSTGRES_HOST="localhost",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Configure only DATABASE_URL", result.stderr)

    def test_rejects_sqlite_database_url_in_production(self):
        result = self.probe(DATABASE_URL="sqlite:////tmp/do-not-create.sqlite3")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DATABASE_URL must use PostgreSQL", result.stderr)

    def test_legacy_sqlite_requires_explicit_temporary_opt_in(self):
        result = self.probe(ALLOW_LEGACY_PRODUCTION_SQLITE="True")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sqlite3", result.stdout)

    def test_worker_requires_broker_even_with_all_billing_flags_off(self):
        result = self.probe(
            SERVICE_ROLE="worker",
            POSTGRES_HOST="postgres", POSTGRES_DB="cubegears",
            POSTGRES_USER="cubegears", POSTGRES_PASSWORD="not-real",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Separate Celery workers", result.stderr)

    def test_committed_templates_never_contain_credentials(self):
        local = dotenv_values(ROOT / ".env.example")
        prod = dotenv_values(ROOT / ".env.production.example")
        for values in (local, prod):
            for key in (
                "EMAIL_HOST_PASSWORD", "RAZORPAY_KEY_SECRET",
                "RAZORPAY_WEBHOOK_SECRET", "WHATSAPP_PLATFORM_ACCESS_TOKEN",
            ):
                self.assertFalse(values.get(key), key)
        self.assertFalse(prod.get("DJANGO_SECRET_KEY"))
        self.assertFalse(prod.get("JWT_SIGNING_KEY"))
        self.assertFalse(prod.get("POSTGRES_PASSWORD"))


if __name__ == "__main__":
    unittest.main()
