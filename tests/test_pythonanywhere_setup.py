"""Tests for the PythonAnywhere .env production setup utility."""
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from dotenv import dotenv_values, set_key

from scripts.setup_pythonanywhere import configure, main, valid_secret


class PythonAnywhereSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env_file = Path(self.tmp.name) / ".env"

    def values(self):
        return dotenv_values(self.env_file)

    def test_first_run_generates_private_strong_keys(self):
        status = configure(self.env_file, "cardflow.pythonanywhere.com")
        values = self.values()
        self.assertTrue(valid_secret(values["DJANGO_SECRET_KEY"]))
        self.assertTrue(valid_secret(values["JWT_SIGNING_KEY"]))
        self.assertNotEqual(values["DJANGO_SECRET_KEY"], values["JWT_SIGNING_KEY"])
        self.assertEqual(values["DJANGO_DEBUG"], "False")
        self.assertIn("cardflow.pythonanywhere.com", values["DJANGO_ALLOWED_HOSTS"])
        self.assertIn(
            "https://cardflow.pythonanywhere.com",
            values["CSRF_TRUSTED_ORIGINS"],
        )
        self.assertEqual(
            values["EMAIL_BACKEND"],
            "django.core.mail.backends.smtp.EmailBackend",
        )
        self.assertEqual(stat.S_IMODE(self.env_file.stat().st_mode), 0o600)
        self.assertIn("generated", status["DJANGO_SECRET_KEY"])

    def test_second_run_never_rotates_keys(self):
        configure(self.env_file, "cardflow.pythonanywhere.com")
        first = self.values()
        configure(self.env_file, "cardflow.pythonanywhere.com")
        second = self.values()
        self.assertEqual(second["DJANGO_SECRET_KEY"], first["DJANGO_SECRET_KEY"])
        self.assertEqual(second["JWT_SIGNING_KEY"], first["JWT_SIGNING_KEY"])
        self.assertEqual(
            second["DJANGO_ALLOWED_HOSTS"].split(",").count("cardflow.pythonanywhere.com"),
            1,
        )

    def test_preserves_legacy_fallback_key_and_custom_smtp(self):
        self.env_file.touch()
        existing = "a" * 64
        set_key(str(self.env_file), "DJANGO_SECRET_KEY", existing)
        set_key(str(self.env_file), "EMAIL_BACKEND", "myapp.email.CustomBackend")
        configure(self.env_file, "cardflow.pythonanywhere.com")
        values = self.values()
        self.assertEqual(values["DJANGO_SECRET_KEY"], existing)
        self.assertNotIn("JWT_SIGNING_KEY", values)
        self.assertEqual(values["EMAIL_BACKEND"], "myapp.email.CustomBackend")

    def test_rotates_only_invalid_sample_values_and_adds_frontend(self):
        self.env_file.touch()
        set_key(
            str(self.env_file),
            "DJANGO_SECRET_KEY",
            "change-this-in-production-use-at-least-32-random-bytes",
        )
        set_key(str(self.env_file), "CORS_ALLOWED_ORIGINS", "https://another.example.com")
        configure(
            self.env_file,
            "cardflow.pythonanywhere.com",
            "https://cubegears-company-panel.vercel.app/",
        )
        values = self.values()
        self.assertTrue(valid_secret(values["DJANGO_SECRET_KEY"]))
        self.assertEqual(values["FRONTEND_URL"], "https://cubegears-company-panel.vercel.app")
        self.assertIn("https://another.example.com", values["CORS_ALLOWED_ORIGINS"])
        self.assertIn(
            "https://cubegears-company-panel.vercel.app",
            values["CORS_ALLOWED_ORIGINS"],
        )

    def test_rejects_non_hostname_and_insecure_frontend_before_writing_file(self):
        with self.assertRaises(ValueError):
            configure(self.env_file, "https://cardflow.pythonanywhere.com")
        with self.assertRaises(ValueError):
            configure(
                self.env_file,
                "cardflow.pythonanywhere.com",
                "http://example.com",
            )
        self.assertFalse(self.env_file.exists())

    def test_weak_shell_override_causes_nonzero_exit_without_printing_secrets(self):
        stdout, stderr = StringIO(), StringIO()
        with patch.dict(os.environ, {"DJANGO_SECRET_KEY": "weak", "DJANGO_DEBUG": "True"}, clear=True):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main([
                    "--host", "cardflow.pythonanywhere.com",
                    "--env-file", str(self.env_file),
                ])
        self.assertEqual(exit_code, 2)
        self.assertIn("shell environment", stderr.getvalue())
        self.assertNotIn(self.values()["DJANGO_SECRET_KEY"], stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
