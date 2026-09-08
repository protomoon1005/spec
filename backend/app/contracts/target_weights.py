"""계약 ④ 목표 비중 벡터 형식 (docs/infra-spec.md 6단계 표 ④).

`TargetWeights { as_of, weights, cash, mapped_weights, group_cap_applications[] }`
— 선형매핑 직후(mapped_weights)와 그룹캡 적용 후 최종(weights)을 둘 다 보존한다.
실제 GroupCapEnforcer는 이번 범위 밖(인터페이스/NotImplementedError만, docs/
infra-spec.md 9장)이므로, 이 목업은 그룹캡이 하나 걸렸다고 가정한 결정론적
샘플 데이터를 만들어 형식만 보여준다 — 실제 asset_groups/group_caps를 조회하지
않는다.
"""
from __future__ import annotations

import hashlib
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

_DEFAULT_CASH = 0.05
_MOCK_SCALE_FACTOR = 0.8  # 첫 종목이 속한 것으로 가정한 그룹이 캡에 걸렸다고 가정한 고정값


class GroupCapApplication(BaseModel):
    """GROUP_CAP_APPLICATIONS 한 행의 형태. sum_before/cap/scale_factor로
    '묶음 상한에 걸려 축소했다'는 설명을 수치로 뒷받침한다."""

    model_config = ConfigDict(extra="forbid")

    group_id: str
    sum_before: float = Field(ge=0)
    cap: float = Field(ge=0)
    scale_factor: float = Field(ge=0, le=1)


class TargetWeights(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: date
    weights: dict[str, float] = Field(description="그룹캡 적용 후 최종 목표 비중")
    cash: float = Field(ge=0, le=1)
    mapped_weights: dict[str, float] = Field(description="선형매핑 직후, 그룹캡 적용 전")
    group_cap_applications: list[GroupCapApplication] = Field(default_factory=list)


def _deterministic_unit(*parts: str) -> float:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
    as_int = int.from_bytes(digest[:8], byteorder="big", signed=False)
    return as_int / float(2**64)


def mock_target_weights(
    tickers: list[str], *, as_of: date, cash: float = _DEFAULT_CASH
) -> TargetWeights:
    """유니버스 티커 목록에 대한 결정론적 목업 TargetWeights.

    mapped_weights는 (1 - cash)를 해시 기반 비례로 나눈 값이다. 그중 첫 티커가
    가상의 그룹캡에 걸렸다고 가정해 scale_factor만큼 줄이고, 줄어든 만큼을
    최종 cash로 흡수한다 — mapped_weights와 weights가 서로 다른 이유를
    group_cap_applications로 설명할 수 있음을 보여주는 것이 이 목업의 목적이다.
    """
    if not tickers:
        raise ValueError("tickers는 최소 1개 이상이어야 한다")

    key_base = (as_of.isoformat(),)
    raw = {t: _deterministic_unit(*key_base, t) for t in tickers}
    total_raw = sum(raw.values())
    pool = 1.0 - cash
    mapped_weights = {t: round(pool * (raw[t] / total_raw), 6) for t in tickers}

    first_ticker = tickers[0]
    sum_before = mapped_weights[first_ticker]
    capped = round(sum_before * _MOCK_SCALE_FACTOR, 6)
    freed = round(sum_before - capped, 6)

    final_weights = dict(mapped_weights)
    final_weights[first_ticker] = capped
    final_cash = round(cash + freed, 6)

    group_cap_applications = [
        GroupCapApplication(
            group_id="MOCK_GROUP",
            sum_before=sum_before,
            cap=capped,
            scale_factor=_MOCK_SCALE_FACTOR,
        )
    ]

    return TargetWeights(
        as_of=as_of,
        weights=final_weights,
        cash=final_cash,
        mapped_weights=mapped_weights,
        group_cap_applications=group_cap_applications,
    )
