# Production backend image. Use the SAME image for Django web, Celery worker and Beat.
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SERVICE_ROLE=web
WORKDIR /app
RUN groupadd --system cubegears && useradd --system --gid cubegears --home-dir /home/cubegears --create-home cubegears
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/requirements.txt
COPY . /app
RUN mkdir -p /var/lib/cubegears/media /var/lib/cubegears/private_job_media /app/staticfiles \
 && chown -R cubegears:cubegears /app /var/lib/cubegears /home/cubegears
USER cubegears
# Build static files without production secrets or a database connection.
RUN python manage.py collectstatic --noinput --clear
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-"]
