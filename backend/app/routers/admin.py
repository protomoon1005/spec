"""admin 라우터. admin 역할만 통과하고, 통과해도 본체는 501이다
(docs/infra-spec.md 7단계, 9장). 하드캡/프리셋 값을 바꾸는 실제 로직은 범위 밖."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from app.core.errors import raise_not_implemented
from app.core.security import AuthUser, require_admin

router = APIRouter(prefix="/admin", tags=["admin"])


class HardcapVersionResponse(BaseModel):
    """HARDCAP_VERSIONS 확정 컬럼만 담는다 (docs/db-erd.md 4.1)."""

    hardcap_version: str
    max_weight_per_asset: float
    cash_min: float
    max_loss_per_trade: float
    max_drawdown: float
    min_interval_days: int
    leverage_allowed: bool
    activated_at: datetime | None


@router.get("/hardcap-versions", response_model=list[HardcapVersionResponse])
def list_hardcap_versions(user: AuthUser = Depends(require_admin)) -> list[HardcapVersionResponse]:
    raise_not_implemented()


@router.post("/hardcap-versions", response_model=HardcapVersionResponse, status_code=status.HTTP_201_CREATED)
def create_hardcap_version(user: AuthUser = Depends(require_admin)) -> HardcapVersionResponse:
    raise_not_implemented()
