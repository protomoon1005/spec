"""Celery 태스크 두 개: compile_spec, run_backtest.

compile_spec 은 M1 6단계에서 실제 컴파일로 교체됐다. 202 Accepted + job_id ->
GET /jobs/{job_id}/stream 패턴은 그대로다 (app/routers/jobs.py, app/routers/spec.py).

run_backtest 는 M4 가 만든 러너를 API 에 잇는 배선이다(2026-09-28). 상태는 job_id 가
아니라 backtest_runs 에 남긴다 — GET /backtest/runs/{run_id} 가 거기서 읽는다.
"""

from __future__ import annotations

import logging

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# 사용자에게 보여 줄 실패 이유. 예상 못 한 오류의 스택은 워커 로그에만 남긴다.
UNEXPECTED_FAILURE = "백테스트 실행 중 예상하지 못한 오류가 났다. 워커 로그를 확인해야 한다."


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
        "strategy_id": result.saved.strategy_id,
        "universe_size": result.saved.universe_size,
        # 사용자가 원했지만 못 담은 것 [{"ticker", "name", "reason_code", "reason"}] (app/m1/excluded.py)
        "excluded": list(result.excluded),
        # 문장에서 읽은 손실 한도와 근거. 없으면 null (app/m1/constraints.py)
        "loss_limit": result.loss_limit.as_dict() if result.loss_limit else None,
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
    from app.repositories import specs

    if specs.get_strategy_v0_2(run["strategy_id"]) is not None:
        return _run_rules_backtest(run_id, run["strategy_id"])

    try:
        backtests.mark_running(run_id, **service.run_conditions())
        result = service.run_spec_backtest(
            run["strategy_id"],
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


def _run_rules_backtest(run_id: int, strategy_id: str) -> dict:
    # 전략서 v0.2 (docs/v0.2-plan.md 7단계). 엔진은 app/backtest/rules_engine.py.
    # 사용자에게 보여 줄 수 있는 실패(데모 범위 밖 등)는 그 이유를 그대로 남긴다
    from app.backtest import rules_engine, rules_service
    from app.contracts.strategy_v0_2 import StrategyV0_2
    from app.repositories import backtests, price_daily, specs

    row = specs.get_strategy_v0_2(strategy_id)
    spec = StrategyV0_2.model_validate(row["spec"])
    try:
        backtests.mark_running(
            run_id,
            fee_rate=rules_engine.FEE_RATE,
            tax_rate=None,
            slippage_bp=rules_engine.SLIPPAGE_RATE * 10_000,
        )
        result = rules_service.run_rules_backtest(spec)
    except (rules_engine.UnsupportedStrategy, ValueError) as exc:
        reason = str(exc)
        if "데모 범위 밖" in reason:
            reason = "코스피200 · 코스닥 · VIX 를 보는 조건은 데모에 데이터가 없어 백테스트할 수 없어요"
        backtests.fail_run(run_id, reason=reason)
        return {"status": backtests.FAILED, "reason": reason}
    except Exception:
        logger.exception("run_backtest(v0.2) %s 실패", run_id)
        backtests.fail_run(run_id, reason=UNEXPECTED_FAILURE)
        return {"status": backtests.FAILED, "reason": UNEXPECTED_FAILURE}

    metrics, window = rules_service.to_run_record(result)
    backtests.finish_run(
        run_id,
        data_snapshot_asof=price_daily.get_latest_trade_date() or spec.backtest.end,
        metrics=metrics,
        window_results=window,
    )
    return {"status": backtests.DONE, "run_id": run_id}


@celery_app.task(name="compile_strategy_v0_2")
def compile_strategy_v0_2(job_payload: dict) -> dict:
    """한국어 문장으로 전략서 v0.2 를 만든다(app/m1/compile_v0_2.py). AI 를 부르므로 작업으로 돈다.

    결과는 설계 문서 "판정 결과 응답 모양"(ok · ask · reject)이고,
    ok 면 "AI가 이해한 전략" 설명(explanation)을 더한다.
    되묻기 답은 AI 를 부르지 않아 작업으로 돌리지 않는다(POST /v2/strategies/compile/{session_id}/answer).
    """
    from app.llm.client import get_llm_client
    from app.m1 import compile_v0_2

    result = compile_v0_2.start(job_payload["user_id"], job_payload["text"], get_llm_client())
    return compile_v0_2.with_explanation(result)
