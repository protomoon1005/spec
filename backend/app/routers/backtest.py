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


@router.post("/runs", response_model=BacktestRunResponse, status_code=status.HTTP_202_ACCEPTED,
             summary="과거 데이터로 전략 돌려보기 · 아직 안 만듦")
def create_backtest_run(
    payload: BacktestRunRequest, user: AuthUser = Depends(require_any_role)
) -> BacktestRunResponse:
    """전략서 하나를 과거 구간에 돌려서 "그때 이걸 했으면 얼마 벌었을까"를 계산한다.

    오래 걸리는 일이라 접수만 하고 번호를 준다.

    같은 전략서라도 돌릴 때마다 조건(어느 시점 데이터를 썼는지 등)이 다를 수 있어서,
    그 조건은 전략서가 아니라 **이 실행 기록에** 남긴다. 나중에 같은 결과를 다시
    만들어 내려면 조건이 남아 있어야 한다.

    **아직 안 만들었다 (501).**
    """
    raise_not_implemented()


@router.get("/runs/{run_id}", response_model=BacktestRunResponse, summary="돌려본 결과 보기 · 아직 안 만듦")
def get_backtest_run(run_id: int, user: AuthUser = Depends(require_any_role)) -> BacktestRunResponse:
    """돌려본 결과와 성적을 본다. 아직 돌아가는 중이면 그 상태가 나온다.

    **아직 안 만들었다 (501).**
    """
    raise_not_implemented()
