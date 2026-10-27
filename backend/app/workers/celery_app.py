"""Celery 앱 팩토리. broker/backend 모두 Redis (REDIS_URL) 를 쓴다.

CELERY_TASK_ALWAYS_EAGER=true 로 두면 .delay()가 브로커 없이 그 자리에서
동기 실행된다 — 워커 컨테이너 없이 단위 테스트를 돌릴 때 쓴다. eager 모드에서도
AsyncResult(job_id)로 상태를 조회할 수 있도록 task_store_eager_result를 켠다.
"""
from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings


def _build_celery_app() -> Celery:
    settings = get_settings()
    app = Celery("spec", broker=settings.redis_url, backend=settings.redis_url)
    app.conf.task_always_eager = settings.celery_task_always_eager
    app.conf.task_eager_propagates = True
    app.conf.task_store_eager_result = True
    app.conf.result_extended = True
    # 매일 라이브 루프. 장 마감(15:30) 뒤 평일 한 번. 휴장일은 거르지 않는다 — 그날 판단은
    # 직전 거래일 가격으로 돌고 data_snapshot_asof 에 그 날이 남는다(docs/known-issues.md).
    app.conf.timezone = "Asia/Seoul"
    app.conf.beat_schedule = {
        "run-live-day": {
            "task": "run_live_day",
            "schedule": crontab(hour=16, minute=0, day_of_week="mon-fri"),
        },
    }
    return app


celery_app = _build_celery_app()
