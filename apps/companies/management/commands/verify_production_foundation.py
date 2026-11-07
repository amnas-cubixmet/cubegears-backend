"""Startup preflight for the separate Django, Redis/Celery and storage services."""

from pathlib import Path
from tempfile import TemporaryFile

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from common.readiness import check_dependencies


class Command(BaseCommand):
    help = "Check database, optional Redis broker and writable media volumes without changing workshop records."

    def add_arguments(self, parser):
        parser.add_argument(
            "--require-redis", action="store_true",
            help="Require a live Redis broker even when REQUIRE_REDIS_READINESS=False.",
        )
        parser.add_argument(
            "--skip-storage", action="store_true",
            help="Skip filesystem checks (useful for stateless/web smoke probes).",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG and settings.DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":
            if not getattr(settings, "ALLOW_LEGACY_PRODUCTION_SQLITE", False):
                raise CommandError("Production database must be PostgreSQL.")

        check_redis = bool(options["require_redis"] or
                           settings.SERVICE_ROLE in {"worker", "beat"} or
                           settings.REQUIRE_REDIS_READINESS)
        ready, components = check_dependencies(check_redis=check_redis)
        if not ready:
            failed = ", ".join(name for name, value in components.items() if value != "ready")
            raise CommandError(f"Production dependency unavailable: {failed}")

        if not options["skip_storage"]:
            for label, configured_path in (
                ("DJANGO_MEDIA_ROOT", settings.MEDIA_ROOT),
                ("PRIVATE_JOB_MEDIA_ROOT", settings.PRIVATE_JOB_MEDIA_ROOT),
            ):
                directory = Path(configured_path)
                if not directory.is_dir():
                    raise CommandError(f"{label} does not exist. Mount and provision the persistent volume.")
                try:
                    with TemporaryFile(dir=directory):
                        pass
                except OSError as exc:
                    raise CommandError(f"{label} is not writable.") from exc

        self.stdout.write(self.style.SUCCESS(
            "CubeGears production dependencies ready: database, "
            + ("Redis, " if check_redis else "")
            + ("persistent media storage" if not options["skip_storage"] else "storage not checked")
        ))
