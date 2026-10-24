#!/usr/bin/env bash
# Run as a dedicated background service (Railway, VPS, systemd, etc.).
# Examples:
#   bash scripts/start_celery.sh worker
#   bash scripts/start_celery.sh beat
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

MODE="${1:-}"
case "$MODE" in
  worker|beat) ;;
  *)
    echo "Usage: bash scripts/start_celery.sh {worker|beat}" >&2
    exit 2
    ;;
esac

PYTHON="${PYTHON_EXECUTABLE:-python}"

# Django reads the deployment environment and the project .env file. Do not
# infer a broker URL, turn on billing flags, or silently run tasks eagerly.
"$PYTHON" - <<'PY'
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()
from django.conf import settings

broker = getattr(settings, "CELERY_BROKER_URL", "")
if not broker.startswith(("redis://", "rediss://")):
    raise SystemExit(
        "CELERY_BROKER_URL must be set to a valid Redis URL in the "
        "deployment environment or private .env file."
    )
if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
    raise SystemExit("Disable CELERY_TASK_ALWAYS_EAGER for production workers.")
print(
    "Celery configuration validated; "
    f"Beat schedules registered: {len(settings.CELERY_BEAT_SCHEDULE)}",
    flush=True,
)
PY

if [[ "$MODE" == "worker" ]]; then
  # One process by default: safer for deployments using SQLite.
  # Explicitly raise only after database concurrency has been reviewed.
  exec "$PYTHON" -m celery -A config worker \
    --loglevel=INFO \
    --concurrency="${CELERY_WORKER_CONCURRENCY:-1}"
fi

# Use one Beat service only. The scheduler state file must be writable.
exec "$PYTHON" -m celery -A config beat \
  --loglevel=INFO \
  --schedule="${CELERY_BEAT_SCHEDULE_FILE:-/tmp/cubegears-celerybeat-schedule}"
