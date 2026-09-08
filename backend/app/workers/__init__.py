"""celery -A app.workers 로 기동하기 위한 진입점. `app` 이름으로 노출한다."""
from __future__ import annotations

from app.workers import tasks  # noqa: F401  임포트 시점에 태스크가 등록된다
from app.workers.celery_app import celery_app

app = celery_app

__all__ = ["app", "celery_app"]
