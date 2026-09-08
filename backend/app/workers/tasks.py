"""Celery 태스크 이름 6종 (docs/infra-spec.md 6~7단계).

compile_spec만 "더미 태스크가 3초 뒤 완료 이벤트를 흘리는 수준"으로 실제
동작한다 — 202 Accepted + job_id -> GET /jobs/{job_id}/stream SSE 패턴을
시연하기 위한 것이다 (app/routers/jobs.py, app/routers/spec.py).

나머지 5개(run_backtest·daily_judge·ingest_market·train_model·
update_view_weights)는 본체 구현이 이번 범위 밖이다(docs/infra-spec.md 9장:
Validator·판단 계층·백테스트 러너·수집기 본체는 안 만든다). 이름만 미리
등록해 다른 라인이 태스크 이름에 의존해 개발을 시작할 수 있게 한다.
"""
from __future__ import annotations

import time

from app.workers.celery_app import celery_app

# 테스트에서 3초 대기를 0으로 줄이기 위한 monkeypatch 지점.
DEMO_SLEEP_SECONDS = 3.0


@celery_app.task(name="compile_spec")
def compile_spec(job_payload: dict) -> dict:
    """더미 컴파일 태스크. 실제 자연어 -> Spec 컴파일(M1)은 이번 범위 밖이다."""
    time.sleep(DEMO_SLEEP_SECONDS)
    return {"status": "completed", "job_payload": job_payload}


def _not_implemented(name: str):
    def _task(*args: object, **kwargs: object) -> None:
        raise NotImplementedError(f"{name} 본체는 이번 단계 범위 밖이다 (docs/infra-spec.md 9장)")

    _task.__name__ = name
    return _task


run_backtest = celery_app.task(name="run_backtest")(_not_implemented("run_backtest"))
daily_judge = celery_app.task(name="daily_judge")(_not_implemented("daily_judge"))
ingest_market = celery_app.task(name="ingest_market")(_not_implemented("ingest_market"))
train_model = celery_app.task(name="train_model")(_not_implemented("train_model"))
update_view_weights = celery_app.task(name="update_view_weights")(_not_implemented("update_view_weights"))
