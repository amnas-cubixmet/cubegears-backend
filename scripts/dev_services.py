#!/usr/bin/env python3
"""Run the local Django server, Celery worker and Celery Beat together.

Use the repository's virtualenv: python scripts/dev_services.py
This is a *development* supervisor; never use it to run production services.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
REDIS_CONTAINER = "cubegears-redis"
CHILDREN: list[tuple[str, subprocess.Popen]] = []


def fail(message: str) -> None:
    raise RuntimeError(message)


def truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def configure_environment() -> dict[str, str]:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
    env = os.environ.copy()

    # Never accidentally start development processes with a deployed .env.
    if not truthy(env.get("DJANGO_DEBUG", "True")):
        fail("DJANGO_DEBUG must be True for local development. Check your .env.")
    if truthy(env.get("SECURE_SSL_REDIRECT", "False")):
        fail("Set SECURE_SSL_REDIRECT=False for local HTTP development.")

    env.setdefault("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
    env.setdefault("CELERY_RESULT_BACKEND", "redis://127.0.0.1:6379/1")
    if not env["CELERY_BROKER_URL"].strip():
        env["CELERY_BROKER_URL"] = "redis://127.0.0.1:6379/0"
    if not env["CELERY_RESULT_BACKEND"].strip():
        env["CELERY_RESULT_BACKEND"] = "redis://127.0.0.1:6379/1"
    if not env["CELERY_BROKER_URL"].startswith(("redis://", "rediss://")):
        fail("CELERY_BROKER_URL must use Redis (redis:// or rediss://).")
    if truthy(env.get("CELERY_TASK_ALWAYS_EAGER", "False")):
        fail("Set CELERY_TASK_ALWAYS_EAGER=False when using a real worker.")
    env["PYTHONUNBUFFERED"] = "1"
    return env


def redis_is_ready(broker_url: str) -> bool:
    from redis import Redis
    from redis.exceptions import RedisError

    try:
        client = Redis.from_url(
            broker_url, socket_connect_timeout=0.7, socket_timeout=0.7
        )
        return bool(client.ping())
    except (RedisError, OSError, ValueError):
        return False


def ensure_redis(broker_url: str) -> bool:
    """Return True only if this launcher started a Docker Redis container."""
    if redis_is_ready(broker_url):
        print("[ok] Redis is already available.", flush=True)
        return False

    parsed = urlparse(broker_url)
    is_local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if not is_local or (parsed.port or 6379) != 6379:
        fail("Redis is unavailable at the configured broker URL. Start Redis first.")
    if not shutil.which("docker"):
        fail(
            "Redis is not running and Docker is not installed. "
            "Start Redis on port 6379, or install/start Docker Desktop."
        )

    info = subprocess.run(
        ["docker", "info"], stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, timeout=10, check=False
    )
    if info.returncode != 0:
        fail("Docker Desktop is not running. Start it or start Redis manually.")

    # Reuse the named container when it exists; create it only the first time.
    started = subprocess.run(
        ["docker", "start", REDIS_CONTAINER],
        capture_output=True, text=True, timeout=20, check=False
    )
    if started.returncode != 0:
        print("[redis] Creating a local Redis Docker container...", flush=True)
        created = subprocess.run(
            [
                "docker", "run", "-d", "--name", REDIS_CONTAINER,
                "-p", "127.0.0.1:6379:6379", "redis:7-alpine"
            ],
            capture_output=True, text=True, timeout=120, check=False
        )
        if created.returncode != 0:
            fail("Unable to start Docker Redis: " + created.stderr.strip())

    for _ in range(30):
        if redis_is_ready(broker_url):
            print("[ok] Redis is running.", flush=True)
            return True
        time.sleep(0.5)
    fail("Redis did not become ready on port 6379.")


def start(name: str, command: list[str], env: dict[str, str]) -> None:
    print(f"[start] {name}: {' '.join(command)}", flush=True)
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
        start_new_session=(os.name != "nt"),
    )
    CHILDREN.append((name, process))


def stop_all() -> None:
    for name, process in reversed(CHILDREN):
        if process.poll() is not None:
            continue
        print(f"[stop] {name}", flush=True)
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
            )
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    for _, process in CHILDREN:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def main() -> int:
    if not (ROOT / "manage.py").is_file():
        fail("manage.py was not found in the backend directory.")
    env = configure_environment()

    print("[check] Django system checks...", flush=True)
    check = subprocess.run(
        [sys.executable, "manage.py", "check"],
        cwd=ROOT, env=env, check=False
    )
    if check.returncode:
        fail("Django check failed. Fix the error above before starting services.")

    redis_started_here = False
    try:
        redis_started_here = ensure_redis(env["CELERY_BROKER_URL"])

        # Store Beat state outside the repository to avoid committing .db files.
        beat_schedule = str(
            Path(tempfile.gettempdir()) / "cubegears-local-celerybeat-schedule"
        )
        start("Django", [sys.executable, "manage.py", "runserver"], env)
        start(
            "Celery worker",
            [sys.executable, "-m", "celery", "-A", "config", "worker",
             "--loglevel=INFO", "--pool=solo", "--concurrency=1"],
            env
        )
        start(
            "Celery Beat",
            [sys.executable, "-m", "celery", "-A", "config", "beat",
             "--loglevel=INFO", f"--schedule={beat_schedule}"],
            env
        )
        print("\n[ready] Django: http://127.0.0.1:8000  (Ctrl+C stops all)\n",
              flush=True)

        while True:
            for name, process in CHILDREN:
                code = process.poll()
                if code is not None:
                    fail(f"{name} exited with code {code}; stopping remaining services.")
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[stop] Ctrl+C received.", flush=True)
        return 0
    finally:
        stop_all()
        if redis_started_here:
            subprocess.run(
                ["docker", "stop", REDIS_CONTAINER],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=30, check=False
            )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as error:
        print(f"[error] {error}", file=sys.stderr)
        raise SystemExit(1) from None
