"""Database/queue readiness must fail closed without leaking configuration."""

from unittest.mock import patch

from django.db import DatabaseError
from django.test import TestCase, override_settings


class ReadinessEndpointTests(TestCase):
    def test_ready_when_database_available(self):
        response = self.client.get("/api/v1/ready/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ready")
        self.assertEqual(response.json()["components"]["database"], "ready")
        self.assertIn("X-Request-ID", response)

    def test_database_failure_returns_503_without_secrets(self):
        with patch("common.readiness.connection.cursor", side_effect=DatabaseError("private-db-host")):
            response = self.client.get("/api/v1/ready/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["components"]["database"], "unavailable")
        self.assertNotIn("private-db-host", response.content.decode())

    @override_settings(REQUIRE_REDIS_READINESS=True, CELERY_BROKER_URL="redis://example.invalid:6379/0")
    def test_redis_failure_does_not_leak_broker_url(self):
        with patch("redis.Redis.from_url", side_effect=OSError("redis://secret-host")):
            response = self.client.get("/api/v1/ready/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["components"]["queue"], "unavailable")
        self.assertNotIn("secret-host", response.content.decode())
