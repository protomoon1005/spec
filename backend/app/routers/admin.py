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


@router.get("/hardcap-versions", response_model=list[HardcapVersionResponse],
            summary="공통 상한선 목록 · 아직 안 만듦 · 관리자만")
def list_hardcap_versions(user: AuthUser = Depends(require_admin)) -> list[HardcapVersionResponse]:
    """성향과 상관없이 **모든 전략에 똑같이 걸리는 상한선** 목록이다.

    한 종목에 최대 몇 %까지 담을 수 있는지, 현금은 최소 몇 % 남겨야 하는지,
    레버리지를 허용할지 같은 것들이 들어 있다.

    성향별 한도(`/profile`)보다 한 겹 더 바깥 울타리다 —
    성향이 공격적이어도 이 선은 못 넘는다.

    **아직 안 만들었다 (501).** 관리자 계정이 아니면 여기 오기 전에 403 이 난다.
    """
    raise_not_implemented()


@router.post("/hardcap-versions", response_model=HardcapVersionResponse, status_code=status.HTTP_201_CREATED,
             summary="공통 상한선 새로 만들기 · 아직 안 만듦 · 관리자만")
def create_hardcap_version(user: AuthUser = Depends(require_admin)) -> HardcapVersionResponse:
    """상한선을 바꿀 때 기존 값을 고치지 않고 **새 버전을 만든다.**

    이미 만들어 둔 전략이 "그때 어떤 상한선으로 만들어졌는지"를 잃지 않게 하려는 것이다.
    값을 덮어쓰면 과거 전략의 근거가 사라진다.

    **아직 안 만들었다 (501).** 관리자만.
    """
    raise_not_implemented()
