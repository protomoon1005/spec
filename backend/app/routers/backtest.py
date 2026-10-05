"""backtest 라우터. 전략서 하나를 과거 구간에 돌리고 결과를 본다.

M4 가 만든 러너를 API 에 잇는 배선이다(2026-09-28). 러너는 워커의 run_backtest
태스크가 app/backtest/service.py 를 거쳐 부르고, 이 라우터는 실행 기록을 넣고
읽기만 한다 — runner·policy 를 import 하지 않는다.
"""
from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.backtest import service
from app.core.security import AuthUser, require_any_role
from app.repositories import backtests, specs
from app.workers.tasks import run_backtest

router = APIRouter(prefix="/backtest", tags=["backtest"])


class BacktestRunRequest(BaseModel):
    spec_id: str
    period_start: date = service.DEFAULT_PERIOD_START
    period_end: date


class BacktestRunResponse(BaseModel):
    """BACKTEST_RUNS 확정 컬럼 일부 (docs/db-erd.md 4.1)."""

    run_id: int
    spec_id: str
    period_start: date | None
    period_end: date | None
    status: str | None
    started_at: datetime | None


class BacktestMetrics(BaseModel):
    """BACKTEST_METRICS. 전략 계열 기준이고 benchmark_cagr 만 시장(069500 보유)이다."""

    cagr: float | None
    mdd: float | None
    sharpe: float | None
    sortino: float | None
    win_rate: float | None
    benchmark_cagr: float | None


class BacktestRunDetail(BacktestRunResponse):
    seed_money: float | None
    fee_rate: float | None
    tax_rate: float | None
    slippage_bp: float | None
    random_seed: int | None
    data_snapshot_asof: date | None
    feature_set_version: str | None
    metrics: BacktestMetrics | None
    # 전략·대조군·시장 계열별 total·cagr·vol·sharpe·sortino·mdd·final
    summary: dict[str, dict[str, float]] | None
    series: list[dict] | None  # done 일 때만. [{date, strategy, control, market}]
    scorer_sources: dict[str, str] | None  # 관점별 real · mock · neutral
    schedule: dict | None
    weight_path: str | None
    reason: str | None  # failed 일 때만
    # 아래는 done 일 때만. 리포트 화면(/report?run_id=)이 비중 표·캡 로그·리밸런싱
    # 이력을 이 실행 그대로 그리는 데 쓴다. 저장은 전부터 하고 있었고 내보내지만
    # 않았다 — 그래서 리포트가 이 실행 대신 미리 저장된 결과 파일을 읽고 있었다.
    risk_level: int | None
    universe: list[dict] | None  # 이 실행이 쓴 종목과 등급·분류·요청 밴드
    decisions: list[dict] | None  # 전략 리밸런싱 결정 (signals·mapped·target·cash·capApplications)
    control_decisions: list[dict] | None  # 대조군(신호 미사용) 결정


@router.post("/runs", response_model=BacktestRunResponse, status_code=status.HTTP_202_ACCEPTED,
             summary="과거 데이터로 전략 돌려보기")
def create_backtest_run(
    payload: BacktestRunRequest, user: AuthUser = Depends(require_any_role)
) -> BacktestRunResponse:
    """전략서 하나를 과거 구간에 돌려서 "그때 이걸 했으면 얼마 벌었을까"를 계산한다.

    오래 걸리는 일이라 접수만 하고 번호(run_id)를 준다. 결과는 GET 으로 본다.
    자기 전략서만 돌릴 수 있다. 없거나 남의 것이면 404.

    같은 전략서라도 돌릴 때마다 조건(어느 시점 데이터를 썼는지 등)이 다를 수 있어서,
    그 조건은 전략서가 아니라 **이 실행 기록에** 남긴다. 나중에 같은 결과를 다시
    만들어 내려면 조건이 남아 있어야 한다.
    """
    spec = specs.get_spec(payload.spec_id)
    if spec is None or spec["user_id"] != user.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="전략서를 찾을 수 없다")

    row = backtests.create_run(
        payload.spec_id,
        period_start=payload.period_start,
        period_end=payload.period_end,
        seed_money=service.SEED_MONEY,
        feature_set_version=service.FEATURE_SET_VERSION,
    )
    run_backtest.delay(row["run_id"])
    return BacktestRunResponse(**row)


@router.get("/runs/{run_id}", response_model=BacktestRunDetail, summary="돌려본 결과 보기")
def get_backtest_run(run_id: int, user: AuthUser = Depends(require_any_role)) -> BacktestRunDetail:
    """돌려본 결과와 성적을 본다. 아직 돌아가는 중이면 그 상태가 나온다.

    상태는 queued → running → done | failed. 곡선은 done 일 때만, 실패 이유는
    failed 일 때만 나온다. 남의 실행 기록이면 404.

    관점별 출처(real · neutral · mock)는 scorer_sources 가 알려 준다 — 감성은 중립 고정이다.
    """
    run = backtests.get_run(run_id)
    if run is None or run["user_id"] != user.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="실행 기록을 찾을 수 없다")

    results = run["window_results"] or {}
    done = run["status"] == backtests.DONE
    return BacktestRunDetail(
        **{key: run[key] for key in BacktestRunDetail.model_fields if key in run},
        metrics=BacktestMetrics(**{key: run[key] for key in BacktestMetrics.model_fields}) if done else None,
        summary=results.get("summary"),
        series=results.get("series") if done else None,
        scorer_sources=results.get("scorer_sources"),
        schedule=results.get("schedule"),
        weight_path=results.get("weight_path"),
        reason=results.get("error"),
        risk_level=results.get("risk_level"),
        # universe 는 2026-09-29 이전 실행에는 없다(그때는 저장하지 않았다). 없으면 None.
        universe=results.get("universe") if done else None,
        decisions=results.get("decisions") if done else None,
        control_decisions=results.get("control_decisions") if done else None,
    )
