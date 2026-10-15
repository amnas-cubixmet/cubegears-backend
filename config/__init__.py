"""Celery application for scheduled background jobs (wallet billing remains opt-in)."""
from .celery import app as celery_app

__all__ = ("celery_app",)
