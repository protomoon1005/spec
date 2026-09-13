"""거시지표 적재 경로 테스트. postgres 가 필요하다.

조회(get_macro)의 경계는 test_asof_repositories.py 가 이미 지키지만, 적재가
released_at 을 잘못 넣으면 조회가 아무리 옳아도 소용없다 — 쓰기 경로로도 확인한다.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.macro.codes import (
    BY_CODE,
    TREND_INDEX_CODES,
    VOLATILITY_INDEX_CODES,
    available_codes,
    estimate_released_at,
)
from app.repositories.macro_indicators import get_macro, upsert_macro

AS_OF = date(2026, 9, 10)


@pytest.fixture
def code(engine):
    generated = f"TEST_{uuid.uuid4().hex[:8].upper()}"
    yield generated
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM macro_indicators WHERE indicator_code = :c"), {"c": generated}
        )


def _count(engine, code: str) -> int:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT count(*) FROM macro_indicators WHERE indicator_code = :c"), {"c": code}
        ).scalar_one()


def test_roundtrip(engine, code) -> None:
    upsert_macro(code, as_of=AS_OF, value=18.5, released_at=AS_OF, source="fred")
    assert get_macro(code, as_of=AS_OF) == pytest.approx(18.5)


def test_reingest_updates_in_place(engine, code) -> None:
    # 거시지표는 잠정치가 확정치로 개정되는 일이 정상이다 — append-only 가 아니다.
    upsert_macro(code, as_of=AS_OF, value=18.5, released_at=AS_OF, source="fred")
    upsert_macro(code, as_of=AS_OF, value=19.2, released_at=AS_OF, source="fred")

    assert _count(engine, code) == 1
    assert get_macro(code, as_of=AS_OF) == pytest.approx(19.2)


def test_value_released_after_as_of_is_not_visible(engine, code) -> None:
    # 공표 전 값이 과거 판단에 섞이면 미래를 미리 보는 것이다.
    upsert_macro(code, as_of=AS_OF, value=99.0, released_at=AS_OF + timedelta(days=1), source="fred")

    assert get_macro(code, as_of=AS_OF) is None
    assert get_macro(code, as_of=AS_OF + timedelta(days=1)) == pytest.approx(99.0)


def test_released_before_as_of_is_rejected(engine, code) -> None:
    with pytest.raises(ValueError, match="보다 빠르다"):
        upsert_macro(
            code, as_of=AS_OF, value=1.0, released_at=AS_OF - timedelta(days=1), source="fred"
        )
    assert _count(engine, code) == 0


# ── 코드 레지스트리 (DB 불필요하지만 같은 관심사라 여기 둔다) ────────


def test_release_lag_is_per_series_not_uniform() -> None:
    # 관측 빈도가 일별이어도 갱신 주기는 시리즈마다 다르다. 전부 +1일로
    # 뭉뚱그리면 낙관적이고, 그 차이만큼 미래를 미리 보게 된다.
    # 2026-09-12 실측: VIXCLS/BAA10Y 2일 지연, DEXKOUS 8일 지연(주 1회 갱신).
    assert estimate_released_at("VIX_CLOSE", AS_OF) == AS_OF + timedelta(days=3)
    assert estimate_released_at("CREDIT_SPREAD_BAA10Y", AS_OF) == AS_OF + timedelta(days=3)
    assert estimate_released_at("USDKRW", AS_OF) == AS_OF + timedelta(days=10)
    assert estimate_released_at("KOSPI200", AS_OF) == AS_OF + timedelta(days=1)


def test_unknown_code_raises() -> None:
    with pytest.raises(ValueError, match="등록되지 않은 지표 코드"):
        estimate_released_at("NOPE", AS_OF)


def test_spec_code_sets_only_contain_registered_codes() -> None:
    # M1 이 Spec 에 넣을 값 집합이다. 등록되지 않은 코드가 새어 들어가면 안 된다.
    for spec_code in TREND_INDEX_CODES + VOLATILITY_INDEX_CODES:
        assert spec_code in BY_CODE


def test_trend_index_is_available(engine) -> None:
    # 2026-09-12 확보. 지수는 ETF 일봉(price_daily)과 자물쇠를 공유하지 않는다 —
    # FinanceDataReader 가 인증 없이 준다.
    assert "KOSPI200" in BY_CODE
    assert BY_CODE["KOSPI200"].available is True
    assert "KOSPI200" in available_codes()
    assert BY_CODE["KOSPI200"].source == "krx"


@pytest.mark.requires_backfill
def test_trend_index_is_actually_queryable(engine) -> None:
    # 코드만 등록하고 데이터가 없으면 M1 이 Spec 에 넣었을 때 조용히 결측이 된다.
    # 실제로 조회되는지까지 본다.
    # scripts/ingest_macro.py 로 백필한 DB가 전제다 — CI의 새 DB(시드만)에는 없어서
    # requires_backfill 로 뺀다. 테스트 안에서 행을 넣으면 이 테스트가 보려는
    # "실데이터가 실제로 있다"가 사라지므로 그렇게 고치지 않는다.
    value = get_macro("KOSPI200", as_of=date(2025, 6, 30))
    assert value is not None and value > 0


def test_sources_match_the_db_check_constraint() -> None:
    for indicator in BY_CODE.values():
        assert indicator.source in {"ecos", "fred", "krx"}
