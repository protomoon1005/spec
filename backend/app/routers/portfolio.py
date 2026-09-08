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


@router.get("/{portfolio_id}", response_model=PortfolioResponse)
def get_portfolio(portfolio_id: int, user: AuthUser = Depends(require_any_role)) -> PortfolioResponse:
    raise_not_implemented()


@router.post("/{portfolio_id}/approve", response_model=PortfolioResponse)
def approve_portfolio(portfolio_id: int, user: AuthUser = Depends(require_any_role)) -> PortfolioResponse:
    raise_not_implemented()
