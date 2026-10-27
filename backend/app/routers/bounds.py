"""bounds 라우터 — 성향별 허용범위 미리보기 (U02R, M4).

성향 하나를 고르면 종목 원장 전부에 대해 "이 성향이 담을 수 있는가, 담는다면 비중을
얼마까지" 를 돌려준다. 화면이 숫자를 직접 계산하지 않는 이유: 이 화면이 보여 주려는 것이
**전략서를 만들 때 AI 양식에 실제로 들어가는 범위**라서다. 프론트 사본(lib/policy.ts)으로
따로 계산하면 서버 계산 순서(상장폐지 → 레버리지 → 성향 등급)가 바뀔 때 화면만 어긋난다.

그래서 계산을 새로 쓰지 않고 M1 후보 판정(app.m1.candidates.select)을 그대로 부른다.
원장 전 종목을 "지목한 종목" 으로 넘기고 보충 목표를 0 으로 두면, 종목마다 컴파일과
같은 규칙으로 통과(허용범위) 또는 탈락(사유 하나)이 정해진다.

허용범위(weight_min · weight_max)는 기준표 값 그대로다 — 컴파일 양식에 이 값이 들어간다.
하드캡의 종목당 상한은 그 뒤 검증기 4단에서 씌워지므로 hardcap_max 로 따로 준다.
읽기만 하고 아무것도 쓰지 않는다.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.core.security import AuthUser, require_any_role
from app.m1 import candidates
from app.repositories import etf_master, presets

router = APIRouter(prefix="/bounds", tags=["bounds"])


class BoundItem(BaseModel):
    ticker: str
    name: str
    risk_tag: str | None
    asset_group_id: str | None
    sector_group_id: str | None
    country_group_id: str | None
    is_leveraged: bool
    active: bool
    # 담을 수 있으면 True. False 면 아래 범위는 null 이고 reason 에 사유 하나.
    allowed: bool
    # 컴파일 양식에 들어가는 범위(기준표 값 그대로)
    weight_min: float | None
    weight_max: float | None
    # 하드캡 종목당 상한을 씌운 상한. 검증기 4단이 이 값으로 깎는다
    hardcap_max: float | None
    reason: str | None


class BoundsResponse(BaseModel):
    risk_level: int
    preset_version: str
    hardcap_version: str | None
    max_weight_per_asset: float | None
    leverage_allowed: bool | None
    # 묶음 상한 {묶음: 상한}. 업종·국가는 성향과 무관한 고정값, SECTOR_OTHER 는 적용 제외 묶음
    group_caps: dict[str, float]
    items: list[BoundItem]


@router.get("", response_model=BoundsResponse, summary="성향별 허용범위 미리보기")
def get_bounds(
    risk_level: int = Query(ge=1, le=5, description="투자 성향 1(안정투자형) ~ 5(공격투자형)"),
    user: AuthUser = Depends(require_any_role),
) -> BoundsResponse:
    """성향 하나에 대해 원장 전 종목의 허용범위와 탈락 사유를 돌려준다.

    전략서 만들기(M1)의 후보 판정과 같은 규칙이다 — 여기서 담을 수 있다고 나온 종목은
    컴파일에서도 같은 범위로 양식에 들어간다.
    """
    records = etf_master.list_all(include_leveraged=True)
    try:
        selected = candidates.select(
            risk_level=risk_level,
            requested_tickers=[record.ticker for record in records],
            target=0,
        )
        group_caps = presets.get_group_caps(risk_level, preset_version=selected.preset_version)
    except LookupError as exc:
        # 기준표가 적재되지 않은 DB — 시드 문제라 사용자가 고칠 수 없다.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    hardcap = presets.get_active_hardcap()
    per_asset = hardcap["max_weight_per_asset"] if hardcap else None
    allowed = {candidate.ticker: candidate for candidate in selected.candidates}
    reasons = {rejection.ticker: rejection.reason for rejection in selected.rejected}

    items = []
    for record in records:
        candidate = allowed.get(record.ticker)
        weight_max = candidate.weight_max if candidate else None
        hardcap_max = weight_max
        if weight_max is not None and per_asset is not None:
            hardcap_max = min(weight_max, per_asset)
        items.append(
            BoundItem(
                ticker=record.ticker,
                name=record.name,
                risk_tag=record.risk_tag,
                asset_group_id=record.asset_group_id,
                sector_group_id=record.sector_group_id,
                country_group_id=record.country_group_id,
                is_leveraged=record.is_leveraged,
                active=record.active,
                allowed=candidate is not None,
                weight_min=candidate.weight_min if candidate else None,
                weight_max=weight_max,
                hardcap_max=hardcap_max,
                reason=reasons.get(record.ticker),
            )
        )

    return BoundsResponse(
        risk_level=risk_level,
        preset_version=selected.preset_version,
        hardcap_version=hardcap["hardcap_version"] if hardcap else None,
        max_weight_per_asset=per_asset,
        leverage_allowed=hardcap["leverage_allowed"] if hardcap else None,
        group_caps=group_caps,
        items=items,
    )
