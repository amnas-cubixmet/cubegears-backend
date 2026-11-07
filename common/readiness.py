"""Production dependency readiness for load balancers and the React workspace.

The public response reports only generic state; no hostnames, credentials,
database names, Redis URLs, stack traces or tenant data are exposed.
"""

from django.conf import settings
from django.db import connection
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


def check_dependencies(*, check_redis=None):
    """Probe the actual configured database and optional required Redis broker."""
    components = {"database": "unavailable"}
    ready = True
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            row = cursor.fetchone()
        if row and row[0] == 1:
            components["database"] = "ready"
        else:
            ready = False
    except Exception:
        # Avoid leaking connection strings or database exception details.
        ready = False

    require_redis = (
        getattr(settings, "REQUIRE_REDIS_READINESS", False)
        if check_redis is None else check_redis
    )
    if require_redis:
        components["queue"] = "unavailable"
        try:
            from redis import Redis
            client = Redis.from_url(
                settings.CELERY_BROKER_URL,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            try:
                if client.ping():
                    components["queue"] = "ready"
                else:
                    ready = False
            finally:
                client.close()
        except Exception:
            ready = False

    return ready, components


class ReadinessView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        ready, components = check_dependencies()
        result = Response(
            {
                "status": "ready" if ready else "unavailable",
                "service": "cubegears-backend",
                "api_version": "v1",
                "components": components,
                "request_id": getattr(request, "request_id", None),
            },
            status=200 if ready else 503,
        )
        result["Cache-Control"] = "no-store"
        return result
