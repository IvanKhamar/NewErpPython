"""Composition root of the Celery worker: `uv run celery -A newerp.worker worker --pool=solo`."""

from celery import Celery

from newerp.config import get_settings

settings = get_settings()

app = Celery("newerp", broker=settings.redis_url, backend=settings.redis_url)
# Modules with @app.task functions go here, e.g. include=["newerp.infrastructure.tasks"].
app.conf.update(include=[], task_acks_late=True, timezone="Europe/Kyiv")
