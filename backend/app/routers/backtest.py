"""backtest 라우터. 경로·응답모델·인증만 열어두고 본체는 501이다
(docs/infra-spec.md 7단계, 9장). 백테스트 러너 실구현은 범위 밖."""
from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from app.core.errors import raise_not_implemented
from app.core.security import AuthUser, require_any_role

router = APIRouter(prefix="/backtest", tags=["backtest"])


class BacktestRunRequest(BaseModel):
    spec_id: str
    period_start: date
    period_end: date


class BacktestRunResponse(BaseModel):
    """BACKTEST_RUNS 확정 컬럼 일부 (docs/db-erd.md 4.1)."""

    run_id: int
    spec_id: str
    period_start: date | None
    period_end: date | None
    status: str | None
    started_at: datetime | None


@router.post("/runs", response_model=BacktestRunResponse, status_code=status.HTTP_202_ACCEPTED)
def create_backtest_run(
    payload: BacktestRunRequest, user: AuthUser = Depends(require_any_role)
) -> BacktestRunResponse:
    raise_not_implemented()


@router.get("/runs/{run_id}", response_model=BacktestRunResponse)
def get_backtest_run(run_id: int, user: AuthUser = Depends(require_any_role)) -> BacktestRunResponse:
    raise_not_implemented()
