"""전략서 v0.2 라우터 (docs/v0.2-plan.md 7단계, 설계 문서 "판정 결과 응답 모양").

v0.1 라우터(/spec, /backtest)는 9단계에서 v0.1 을 지울 때까지 그대로 둔다. v0.2 는 /v2 아래에 둔다.

- POST /v2/strategies/compile                       한국어 문장 접수(202). 결과는 GET /jobs/{job_id}/stream
- POST /v2/strategies/compile/{session_id}/answer   되묻기에 답하기. AI 를 부르지 않아 바로 결과를 준다
- GET  /v2/strategies/{strategy_id}                 전략서, "AI가 이해한 전략" 설명, 검사 결과
- POST /v2/strategies/{strategy_id}/backtest        백테스트 접수(202). 결과는 GET /v2/backtests/{run_id}
- GET  /v2/backtests/{run_id}                       백테스트 결과(평가액 곡선, 체결, 거래, 비교 기준, 안내)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.contracts.strategy_v0_2 import StrategyV0_2
from app.core.security import AuthUser, require_user
from app.m1 import compile_v0_2, explain
from app.repositories import backtests, specs
from app.workers.tasks import compile_strategy_v0_2, run_backtest

router = APIRouter(prefix="/v2", tags=["strategy v0.2"])

FEATURE_SET_VERSION = "v0.2-rules"


class CompileRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500, description="한국어 전략 문장")


class Accepted(BaseModel):
    job_id: str


class Answer(BaseModel):
    question_id: str
    choice_id: str | None = None
    text: str | None = Field(default=None, max_length=200)


class AnswerRequest(BaseModel):
    answers: list[Answer] = Field(min_length=1)


class BacktestAccepted(BaseModel):
    run_id: int
    status: str | None


@router.post(
    "/strategies/compile",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=Accepted,
    summary="한국어 문장으로 전략서 만들기",
)
def compile_strategy(payload: CompileRequest, user: AuthUser = Depends(require_user)) -> Accepted:
    """문장을 접수하고 작업 번호를 준다. `GET /jobs/{job_id}/stream` 으로 결과를 받는다.

    결과(`result`)는 셋 중 하나다(설계 문서 "판정 결과 응답 모양").

    - `ok`: 전략서를 draft 로 저장했다. `strategy_id`, `spec`, `checks`, `explanation`
    - `ask`: 되묻는다. `session_id`, `round`, `remaining_total`, `questions`(선택지 id 와 글자)
    - `reject`: 만들 수 없다. `code`, `message`, `evidence`, `stage`

    AI(qwen3:8b)를 한두 번 부르므로 10~30초 걸린다.
    """
    job = compile_strategy_v0_2.delay({"user_id": user.user_id, "text": payload.text})
    return Accepted(job_id=job.id)


@router.post("/strategies/compile/{session_id}/answer", summary="되묻기에 답하기")
def answer_questions(session_id: str, payload: AnswerRequest, user: AuthUser = Depends(require_user)) -> dict:
    """질문마다 선택지 id(`choice_id`)나 직접 쓴 답(`text`)을 보낸다. 결과 모양은 접수 결과와 같다.

    AI 를 다시 부르지 않고 바로 결과를 준다. 대화는 30분 뒤 만료된다(404).
    """
    try:
        result = compile_v0_2.answer(
            session_id, [a.model_dump() for a in payload.answers], user_id=user.user_id
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return compile_v0_2.with_explanation(result)


@router.get("/strategies/{strategy_id}", summary="전략서와 설명 보기")
def get_strategy(strategy_id: str, user: AuthUser = Depends(require_user)) -> dict:
    row = _own_strategy(strategy_id, user)
    spec = StrategyV0_2.model_validate(row["spec"])
    checks = [
        {"stage": log["stage"], "passed": log["passed"], "items": log["violations"] or []}
        for log in specs.latest_validation(strategy_id)
    ]
    return {
        "strategy_id": row["strategy_id"],
        "name": row["name"],
        "status": row["status"],
        "input_prompt": row["input_prompt"],
        "spec": row["spec"],
        "explanation": explain.explain(spec, compile_v0_2.names_for(spec)),
        "checks": checks,
    }


@router.post(
    "/strategies/{strategy_id}/backtest",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=BacktestAccepted,
    summary="전략서로 백테스트하기",
)
def backtest_strategy(strategy_id: str, user: AuthUser = Depends(require_user)) -> BacktestAccepted:
    """기간과 시작 금액은 전략서(`spec.backtest`)를 따른다. 결과는 `GET /v2/backtests/{run_id}`."""
    row = _own_strategy(strategy_id, user)
    spec = StrategyV0_2.model_validate(row["spec"])
    run = backtests.create_run(
        strategy_id,
        period_start=spec.backtest.start,
        period_end=spec.backtest.end,
        seed_money=spec.backtest.capital,
        feature_set_version=FEATURE_SET_VERSION,
    )
    run_backtest.delay(run["run_id"])
    return BacktestAccepted(run_id=run["run_id"], status=run["status"])


@router.get("/backtests/{run_id}", summary="백테스트 결과 보기")
def get_backtest(run_id: int, user: AuthUser = Depends(require_user)) -> dict:
    """상태는 queued, running, done, failed. done 이면 결과 전체, failed 면 이유(`reason`)를 준다.

    결과: `metrics`(연환산 수익률 · 최대 낙폭 · 승률 · 비교 기준 수익률), `summary`(전략 · 같은 종목 보유 ·
    KODEX 200 보유), `series`(날짜별 평가액 세 줄), `fills`, `trades`, `unfilled`, `holdings_at_end`, `notes`
    """
    run = backtests.get_run(run_id)
    if run is None or run["user_id"] != user.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="실행 기록을 찾을 수 없어요")
    window = run["window_results"] or {}
    out = {
        key: run[key]
        for key in (
            "run_id",
            "strategy_id",
            "period_start",
            "period_end",
            "seed_money",
            "fee_rate",
            "slippage_bp",
            "data_snapshot_asof",
            "status",
            "started_at",
        )
    }
    if run["status"] == backtests.DONE:
        out["metrics"] = {key: run[key] for key in ("cagr", "mdd", "win_rate", "benchmark_cagr")}
        out.update({k: v for k, v in window.items() if k != "engine"})
    elif run["status"] == backtests.FAILED:
        out["reason"] = window.get("error")
    return out


def _own_strategy(strategy_id: str, user: AuthUser) -> dict:
    row = specs.get_strategy_v0_2(strategy_id)
    if row is None or row["user_id"] != user.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="전략서를 찾을 수 없어요")
    return row
