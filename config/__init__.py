"""Expose the configured Celery app to Django and ``celery -A config``.

Importing the app only registers configuration/tasks. It does not start a
worker, connect to Redis, send email or enable billing schedules.
"""
from .celery import app as celery_app

__all__ = ("celery_app",)
