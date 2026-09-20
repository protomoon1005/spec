"""허용범위 프리셋·그룹 상한·시스템 하드캡 조회 (M1 2단계).

시점 규약 대상이 아니다 — 기준표는 버전으로 고정되지 시점으로 잘리지 않는다.
대신 **버전을 명시적으로 받는다.** 성향을 확정할 때 박아 둔 기준표 버전을 그대로
넘기면, 운영자가 나중에 값을 바꿔도 그 전략의 산정 근거가 흔들리지 않는다.
버전을 생략하면 활성 버전을 쓴다(신규 생성 경로).

## 같은 수치가 코드에도 있다

backend/app/backtest/policy.py 와 frontend/lib/policy.ts 가 같은 값을 상수로 들고
있다. 백테스트가 데이터베이스 없이 고정된 표 파일만으로 돌게 하려는 것이라 일부러
남겨 둔 사본이다. **정본은 db/seeds 이고 이 모듈이 그걸 읽는다.**
어긋나면 tests/test_presets_repo.py 가 실패한다.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text

from app.core.db import get_engine

# 산업 섹터가 아니라 "분류되지 않음" 버킷이다. 시장대표·국채·원자재가 전부 여기로
# 들어와서 173종목 중 138개가 여기 있다. 데이터베이스에는 다른 섹터와 같은 30%
# 상한이 들어 있지만, 그대로 걸면 정상 포트폴리오가 항상 걸린다.
# **상한을 적용하는 쪽에서 이 묶음을 빼고 써야 한다.**
CAP_EXEMPT_GROUPS = frozenset({"SECTOR_OTHER"})

# 성향 무관 고정 상한(업종·국가)은 risk_level 0 으로 들어 있다.
RISK_LEVEL_ANY = 0


def _resolve_version(preset_version: str | None) -> str:
    """버전을 정하고, 없으면 바로 실패시킨다.

    **빈 사전을 돌려주면 안 된다.** 이 값들은 안전 상한이라, 호출부가
    `bounds.get(등급)` 으로 None 을 받고 "상한이 없다 = 제한 없음" 으로 읽으면
    막아야 할 것을 통과시킨다. 없으면 없다고 터뜨리는 편이 안전하다.
    """
    version = preset_version or active_preset_version()
    if version is None:
        raise LookupError("활성 기준표가 없다 — db/seeds/02_preset_v0_1.sql 적재 여부를 확인할 것")
    return version


@dataclass(frozen=True)
class AssetBound:
    preset_id: int
    risk_tag: str
    allowed_min: float
    allowed_max: float


def active_preset_version() -> str | None:
    """활성 기준표 버전. 없으면 None.

    활성 버전이 둘이면 안 되지만 그걸 막는 제약이 표에 없다(관리자 경로가 아직
    501 이라 지금은 생길 일이 없다). 그래도 **정렬 없이 LIMIT 1 을 하면 어느 행이
    나올지 정해지지 않는다** — 같은 설문에 다른 버전이 박힐 수 있고 그러면 재현성이
    깨진다. 버전 문자열로 정렬해 항상 같은 답이 나오게 한다.
    """
    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT preset_version FROM preset_versions
                 WHERE is_active = true
                 ORDER BY preset_version
                 LIMIT 1
                """
            )
        ).one_or_none()
    return row[0] if row is not None else None


def list_group_ids(group_level: int) -> list[str]:
    """묶음 이름 목록. 1 이면 자산군, 2 면 업종·국가."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            text("SELECT group_id FROM asset_groups WHERE group_level = :lv ORDER BY group_id"),
            {"lv": group_level},
        ).all()
    return [row.group_id for row in rows]


def get_asset_bounds(risk_level: int, *, preset_version: str | None = None) -> dict[str, AssetBound]:
    """한 성향의 등급별 허용범위 전부. {등급: 범위}.

    등급 하나만 필요해도 이 함수를 쓴다 — 여섯 줄짜리라 나눠 부를 이유가 없고,
    종목 후보를 추릴 때는 어차피 전 등급이 필요하다.
    """
    version = _resolve_version(preset_version)
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT preset_id, risk_tag, allowed_min, allowed_max
                  FROM asset_bound_presets
                 WHERE preset_version = :version AND risk_level = :risk_level
                 ORDER BY risk_tag
                """
            ),
            {"version": version, "risk_level": risk_level},
        ).all()
    if not rows:
        raise LookupError(f"기준표 {version} 에 성향 {risk_level} 의 허용범위가 없다")
    return {
        row.risk_tag: AssetBound(
            preset_id=row.preset_id,
            risk_tag=row.risk_tag,
            allowed_min=float(row.allowed_min),
            allowed_max=float(row.allowed_max),
        )
        for row in rows
    }


def get_group_caps(risk_level: int, *, preset_version: str | None = None) -> dict[str, float]:
    """한 성향에 걸리는 묶음 상한 전부. {묶음: 상한}.

    자산군(주식·채권·원자재)은 성향마다 다르고, 업종·국가는 성향과 무관하게
    고정이라 risk_level 0 으로 들어 있다. 둘을 합쳐 돌려준다.

    **분류되지 않음 묶음(CAP_EXEMPT_GROUPS)도 그대로 들어 있다.** 데이터베이스에
    있는 값을 숨기지 않기 위해서다 — 적용하는 쪽에서 빼고 써야 한다.
    """
    version = _resolve_version(preset_version)
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT group_id, max_total_weight
                  FROM group_caps
                 WHERE preset_version = :version
                   AND risk_level IN (:risk_level, :any_level)
                 ORDER BY group_id
                """
            ),
            {"version": version, "risk_level": risk_level, "any_level": RISK_LEVEL_ANY},
        ).all()
    if not rows:
        raise LookupError(f"기준표 {version} 에 성향 {risk_level} 의 묶음 상한이 없다")
    return {row.group_id: float(row.max_total_weight) for row in rows}


def get_active_hardcap() -> dict | None:
    """성향과 무관하게 모든 전략에 걸리는 시스템 상한. 활성 버전 한 건."""
    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT hardcap_version, max_weight_per_asset, cash_min, max_loss_per_trade,
                       max_drawdown, min_interval_days, leverage_allowed
                  FROM hardcap_versions
                 WHERE activated_at IS NOT NULL
                 ORDER BY activated_at DESC, hardcap_version DESC
                 LIMIT 1
                """
            )
        ).one_or_none()
    if row is None:
        return None
    data = dict(row._mapping)
    for key in ("max_weight_per_asset", "cash_min", "max_loss_per_trade", "max_drawdown"):
        data[key] = float(data[key])
    return data
