"""portfolio 라우터. 경로·응답모델·인증만 열어두고 본체는 501이다
(docs/infra-spec.md 7단계, 9장)."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.errors import raise_not_implemented
from app.core.security import AuthUser, require_any_role

router = APIRouter(prefix="/portfolios", tags=["portfolio"])


class PortfolioResponse(BaseModel):
    """PORTFOLIOS 확정 컬럼만 담는다 (docs/db-erd.md 4.1)."""

    portfolio_id: int
    spec_id: str
    mode: str
    status: str | None
    activated_at: datetime | None


@router.get("/{portfolio_id}", response_model=PortfolioResponse,
            summary="굴리는 중인 전략 상태 보기 · 아직 안 만듦")
def get_portfolio(portfolio_id: int, user: AuthUser = Depends(require_any_role)) -> PortfolioResponse:
    """지금 굴리고 있는 전략 하나의 상태를 본다. 모의투자라 실제 돈은 안 쓴다.

    **아직 안 만들었다 (501).**
    """
    raise_not_implemented()


@router.post("/{portfolio_id}/approve", response_model=PortfolioResponse,
             summary="전략 승인하고 굴리기 시작 · 아직 안 만듦")
def approve_portfolio(portfolio_id: int, user: AuthUser = Depends(require_any_role)) -> PortfolioResponse:
    """사용자가 전략서를 승인해서 굴리기 시작한다.

    승인한 뒤에는 전략서를 **고칠 수 없다.** 데이터베이스가 막아 놨다.
    바꾸고 싶으면 새로 만들어야 한다 — 승인한 내용과 실제로 굴러간 내용이
    달라지면 나중에 "왜 이렇게 샀는지" 설명할 수 없기 때문이다.

    **아직 안 만들었다 (501).**
    """
    raise_not_implemented()
