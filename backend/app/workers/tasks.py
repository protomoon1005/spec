"""Celery 태스크 이름 6종 (docs/infra-spec.md 6~7단계).

compile_spec 은 M1 6단계에서 실제 컴파일로 교체됐다. 202 Accepted + job_id ->
GET /jobs/{job_id}/stream 패턴은 그대로다 (app/routers/jobs.py, app/routers/spec.py).

run_backtest 는 M4 가 만든 러너를 API 에 잇는 배선이다(2026-09-28). 상태는 job_id 가
아니라 backtest_runs 에 남긴다 — GET /backtest/runs/{run_id} 가 거기서 읽는다.

나머지 4개(daily_judge·ingest_market·train_model·update_view_weights)는 본체
구현이 이번 범위 밖이다(docs/infra-spec.md 9장: Validator·판단 계층·수집기
본체는 안 만든다). 이름만 미리 등록해 다른 라인이 태스크 이름에 의존해 개발을
시작할 수 있게 한다.
"""
from __future__ import annotations

import logging

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# 사용자에게 보여 줄 실패 이유. 예상 못 한 오류의 스택은 워커 로그에만 남긴다.
UNEXPECTED_FAILURE = "백테스트 실행 중 예상하지 못한 오류가 났다. 워커 로그를 확인해야 한다."

# 테스트에서 3초 대기를 0으로 줄이기 위한 monkeypatch 지점.


@celery_app.task(name="compile_spec")
def compile_spec(job_payload: dict) -> dict:
    """자연어 -> 전략서 컴파일 (M1).

    새 요청이면 user_id 와 input_prompt 를, 되묻기에 답하는 것이면 session_id 와
    answer 를 받는다. 되물을 것이 있으면 질문을 담아 돌려주고 거기서 멈춘다.

    **오래 걸린다** — 2026-09-20 실측으로 2분을 넘는다. 진행 상황 스트림의 상한이
    그래서 10분이다(app/routers/jobs.py).

    파이프라인은 함수 안에서 import 한다. M1 전용 의존성이 안 깔린 환경에서도
    워커가 뜨고 나머지 태스크가 동작해야 한다.
    """
    from app.llm.client import get_llm_client
    from app.m1 import pipeline
    from app.m1.postprocess import CompileError

    client = get_llm_client()
    try:
        if job_payload.get("session_id"):
            result = pipeline.answer(job_payload["session_id"], job_payload["answer"], client)
        else:
            result = pipeline.start(job_payload["user_id"], job_payload["input_prompt"], client)
    except CompileError as exc:
        # 사용자에게 그대로 보여 줄 수 있는 실패다. 스택을 올리지 않는다.
        return {"status": "failed", "reason": str(exc)}

    if result.status == pipeline.STATUS_NEED_ANSWER:
        return {
            "status": result.status,
            "session_id": result.session_id,
            "question": result.question.text,
            "choices": list(result.question.choices),
        }
    return {
        "status": result.status,
        "spec_id": result.saved.spec_id,
        "universe_size": result.saved.universe_size,
    }


@celery_app.task(name="run_backtest")
def run_backtest(run_id: int) -> dict:
    """queued 인 실행 기록 하나를 돌려 결과를 저장한다.

    러너는 app/backtest/service.py 의 함수 하나로만 부른다 — 비중 계산 경로가
    바뀌어도 이 태스크는 그대로다. 실패는 전부 failed 로 끝내고 이유를 남긴다.
    """
    from app.backtest import service
    from app.repositories import backtests

    run = backtests.get_run(run_id)
    if run is None:
        return {"status": backtests.FAILED, "reason": f"실행 기록이 없다: {run_id}"}

    try:
        backtests.mark_running(run_id, **service.run_conditions())
        result = service.run_spec_backtest(
            run["spec_id"],
            period_start=run["period_start"],
            period_end=run["period_end"],
            seed_money=run["seed_money"],
        )
    except service.BacktestFailed as exc:
        backtests.fail_run(run_id, reason=str(exc))
        return {"status": backtests.FAILED, "reason": str(exc)}
    except Exception:
        logger.exception("run_backtest %s 실패", run_id)
        backtests.fail_run(run_id, reason=UNEXPECTED_FAILURE)
        return {"status": backtests.FAILED, "reason": UNEXPECTED_FAILURE}

    backtests.finish_run(run_id, **result)
    return {"status": backtests.DONE, "run_id": run_id}


def _not_implemented(name: str):
    def _task(*args: object, **kwargs: object) -> None:
        raise NotImplementedError(f"{name} 본체는 이번 단계 범위 밖이다 (docs/infra-spec.md 9장)")

    _task.__name__ = name
    return _task


daily_judge = celery_app.task(name="daily_judge")(_not_implemented("daily_judge"))
ingest_market = celery_app.task(name="ingest_market")(_not_implemented("ingest_market"))
train_model = celery_app.task(name="train_model")(_not_implemented("train_model"))
update_view_weights = celery_app.task(name="update_view_weights")(_not_implemented("update_view_weights"))
