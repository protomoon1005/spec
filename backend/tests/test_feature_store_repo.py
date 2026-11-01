"""upsert_features 왕복·덮어쓰기 테스트 (T2 쓰기 경로).

postgres 가 필요하다. 피처 계산 자체의 회귀는 test_market_features.py 가
의존성 없이 따로 지킨다.

feature_store.ticker 에는 FK 가 없어서 etf_master 가 0행이어도 임의의 테스트용
티커를 쓸 수 있다 — conftest 의 portfolio_id 픽스처도 필요 없다.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.repositories.feature_store import get_features, upsert_features

AS_OF = date(2026, 9, 10)
TOMORROW = AS_OF + timedelta(days=1)
FEATURE_SET_VERSION = "v0.1-test"


@pytest.fixture
def ticker(engine):
    tk = f"T2TEST{uuid.uuid4().hex[:6].upper()}"
    yield tk
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM feature_store WHERE ticker = :ticker"), {"ticker": tk})


def _row_count(engine, ticker: str) -> int:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT count(*) FROM feature_store WHERE ticker = :ticker"), {"ticker": ticker}
        ).scalar_one()


def test_roundtrip(engine, ticker) -> None:
    features = {"rsi_14": 61.25, "ma_gap_20": -0.0132, "vol_20": None}
    upsert_features(
        ticker, as_of=AS_OF, feature_set_version=FEATURE_SET_VERSION, features=features
    )

    assert get_features(ticker, as_of=AS_OF, feature_set_version=FEATURE_SET_VERSION) == features


def test_same_key_twice_overwrites_and_keeps_one_row(engine, ticker) -> None:
    # view_weights 와 달리 피처는 덮어쓰는 게 정상이다 (트리거도 없다).
    upsert_features(
        ticker, as_of=AS_OF, feature_set_version=FEATURE_SET_VERSION, features={"rsi_14": 10.0}
    )
    upsert_features(
        ticker, as_of=AS_OF, feature_set_version=FEATURE_SET_VERSION, features={"rsi_14": 90.0}
    )

    assert _row_count(engine, ticker) == 1
    stored = get_features(ticker, as_of=AS_OF, feature_set_version=FEATURE_SET_VERSION)
    assert stored == {"rsi_14": 90.0}


def test_different_feature_set_versions_coexist(engine, ticker) -> None:
    upsert_features(ticker, as_of=AS_OF, feature_set_version="v0.1-a", features={"x": 1.0})
    upsert_features(ticker, as_of=AS_OF, feature_set_version="v0.2-b", features={"x": 2.0})

    assert _row_count(engine, ticker) == 2
    assert get_features(ticker, as_of=AS_OF, feature_set_version="v0.1-a") == {"x": 1.0}
    assert get_features(ticker, as_of=AS_OF, feature_set_version="v0.2-b") == {"x": 2.0}


def test_tomorrow_features_do_not_leak_into_today(engine, ticker) -> None:
    # 경계 테스트는 test_asof_repositories.py 에 이미 있지만, 쓰기 경로로도 한 번
    # 확인한다 — 적재가 as_of 를 잘못 넣으면 조회가 아무리 옳아도 소용없다.
    upsert_features(
        ticker, as_of=AS_OF, feature_set_version=FEATURE_SET_VERSION, features={"marker": "today"}
    )
    upsert_features(
        ticker,
        as_of=TOMORROW,
        feature_set_version=FEATURE_SET_VERSION,
        features={"marker": "tomorrow"},
    )

    assert get_features(ticker, as_of=AS_OF, feature_set_version=FEATURE_SET_VERSION) == {
        "marker": "today"
    }
    assert get_features(ticker, as_of=TOMORROW, feature_set_version=FEATURE_SET_VERSION) == {
        "marker": "tomorrow"
    }


def test_missing_value_stays_null_not_zero(engine, ticker) -> None:
    # 결측을 0으로 채우면 "지표가 0"과 "이력이 모자람"이 구분되지 않는다.
    upsert_features(
        ticker, as_of=AS_OF, feature_set_version=FEATURE_SET_VERSION, features={"rsi_14": None}
    )
    stored = get_features(ticker, as_of=AS_OF, feature_set_version=FEATURE_SET_VERSION)

    assert stored == {"rsi_14": None}
    assert stored["rsi_14"] is None  # 0.0 이 아니라 null 로 남아야 한다


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_non_finite_value_is_rejected_before_writing(engine, ticker, bad: float) -> None:
    with pytest.raises(ValueError, match="유한하지 않다"):
        upsert_features(
            ticker, as_of=AS_OF, feature_set_version=FEATURE_SET_VERSION, features={"rsi_14": bad}
        )
    assert _row_count(engine, ticker) == 0


def test_empty_features_rejected(engine, ticker) -> None:
    with pytest.raises(ValueError, match="비어 있다"):
        upsert_features(
            ticker, as_of=AS_OF, feature_set_version=FEATURE_SET_VERSION, features={}
        )
    assert _row_count(engine, ticker) == 0
