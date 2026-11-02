"""Celery 태스크 두 개: compile_strategy_v0_2, run_backtest.

compile_strategy_v0_2 는 문장을 전략서 v0.2 로 바꾼다. 202 Accepted + job_id ->
GET /jobs/{job_id}/stream 패턴이다 (app/routers/jobs.py, app/routers/strategy_v0_2.py).

run_backtest 는 전략서 v0.2 를 돌린다. 상태는 job_id 가 아니라 backtest_runs 에 남긴다.
GET /v2/backtests/{run_id} 가 거기서 읽는다.
"""

from __future__ import annotations

import logging

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# 사용자에게 보여 줄 실패 이유. 예상 못 한 오류의 스택은 워커 로그에만 남긴다.
UNEXPECTED_FAILURE = "백테스트 실행 중 예상하지 못한 오류가 났다. 워커 로그를 확인해야 한다."


@celery_app.task(name="run_backtest")
def run_backtest(run_id: int) -> dict:
    # queued 인 실행 기록 하나를 돌려 결과를 저장한다. 실패는 전부 failed 로 끝내고 이유를 남긴다
    from app.repositories import backtests, specs

    run = backtests.get_run(run_id)
    if run is None:
        return {"status": backtests.FAILED, "reason": f"실행 기록이 없다: {run_id}"}
    if specs.get_strategy_v0_2(run["strategy_id"]) is None:
        backtests.fail_run(run_id, reason="전략서 v0.2 가 아니다")
        return {"status": backtests.FAILED, "reason": "전략서 v0.2 가 아니다"}
    return _run_rules_backtest(run_id, run["strategy_id"])


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
