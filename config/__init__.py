"""CubixGear Django package.

Celery is disabled for WSGI deployments without a worker. Keep config/celery.py
for when a broker and worker are provisioned in the future.
"""

# To re-enable Celery, uncomment these lines and install celery[redis]:
# from .celery import app as celery_app
# __all__ = ("celery_app",)
