"""app/repositories/ as_of 경계 테스트 (docs/infra-spec.md 5단계).

세 함수 모두 미래 정보를 반환하면 안 된다는 것이 이 저장소의 존재 이유다.
아래 경계 테스트가 먼저 실패하는 것을 확인한 뒤 구현했다:

  - get_features:      as_of 다음날 데이터를 넣고 반환되지 않음을 단언
  - get_macro:          released_at 이 미래인 행이 반환되지 않음을 단언
  - get_view_weights:   as_of 당일 행이 반환되지 않음을 단언 (부등호가 < 이지 <= 가 아님)
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.repositories.feature_store import get_features
from app.repositories.macro_indicators import get_macro
from app.repositories.view_weights import get_view_weights

FEATURE_SET_VERSION = "v_test"
TODAY = date(2026, 9, 9)
TOMORROW = TODAY + timedelta(days=1)
YESTERDAY = TODAY - timedelta(days=1)


@pytest.fixture
def ticker(engine):
    tk = f"TEST{uuid.uuid4().hex[:8].upper()}"
    yield tk
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM feature_store "
                "WHERE ticker = :ticker AND feature_set_version = :fsv"
            ),
            {"ticker": tk, "fsv": FEATURE_SET_VERSION},
        )


@pytest.fixture
def indicator_code(engine):
    code = f"TEST_{uuid.uuid4().hex[:8].upper()}"
    yield code
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM macro_indicators WHERE indicator_code = :code"), {"code": code}
        )


def _insert_feature(engine, ticker: str, as_of: date, features: dict) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO feature_store (ticker, as_of, feature_set_version, features) "
                "VALUES (:ticker, :as_of, :fsv, CAST(:features AS jsonb))"
            ),
            {
                "ticker": ticker,
                "as_of": as_of,
                "fsv": FEATURE_SET_VERSION,
                "features": '{"marker": "%s"}' % features["marker"],
            },
        )


def _insert_macro(engine, code: str, as_of: date, released_at: date, value: float) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO macro_indicators (indicator_code, as_of, value, released_at, source) "
                "VALUES (:code, :as_of, :value, :released_at, 'ecos')"
            ),
            {"code": code, "as_of": as_of, "value": value, "released_at": released_at},
        )


def _insert_view_weight(engine, portfolio_id: int, as_of: date, view_type: str, weight: float) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO view_weights (portfolio_id, as_of, view_type, weight) "
                "VALUES (:pid, :as_of, :view_type, :weight)"
            ),
            {"pid": portfolio_id, "as_of": as_of, "view_type": view_type, "weight": weight},
        )


# ---------------------------------------------------------------------------
# get_features
# ---------------------------------------------------------------------------


def test_get_features_excludes_future_asof(engine, ticker):
    """as_of 다음날 데이터를 넣으면, as_of=TODAY 조회에서 그 데이터가 나오면 안 된다."""
    _insert_feature(engine, ticker, TOMORROW, {"marker": "future"})

    result = get_features(ticker, as_of=TODAY, feature_set_version=FEATURE_SET_VERSION)

    assert result is None


def test_get_features_returns_latest_as_of_le(engine, ticker):
    """as_of 이전 최신 행은 정상적으로 반환된다 (양성 경로)."""
    _insert_feature(engine, ticker, YESTERDAY, {"marker": "yesterday"})
    _insert_feature(engine, ticker, TODAY, {"marker": "today"})
    _insert_feature(engine, ticker, TOMORROW, {"marker": "future"})

    result = get_features(ticker, as_of=TODAY, feature_set_version=FEATURE_SET_VERSION)

    assert result == {"marker": "today"}


# ---------------------------------------------------------------------------
# get_macro
# ---------------------------------------------------------------------------


def test_get_macro_excludes_future_released_at(engine, indicator_code):
    """as_of는 과거지만 released_at이 미래인 행(아직 공표 전 개정치)은 반환되면 안 된다."""
    _insert_macro(engine, indicator_code, as_of=YESTERDAY, released_at=TOMORROW, value=999.0)

    result = get_macro(indicator_code, as_of=TODAY)

    assert result is None


def test_get_macro_returns_released_value(engine, indicator_code):
    """as_of와 released_at이 모두 조회 시점 이전이면 정상 반환된다 (양성 경로)."""
    _insert_macro(engine, indicator_code, as_of=YESTERDAY, released_at=YESTERDAY, value=3.5)
    _insert_macro(engine, indicator_code, as_of=TODAY, released_at=TOMORROW, value=999.0)

    result = get_macro(indicator_code, as_of=TODAY)

    assert result == 3.5


# ---------------------------------------------------------------------------
# get_view_weights
# ---------------------------------------------------------------------------


def test_get_view_weights_excludes_same_day(engine, portfolio_id):
    """as_of 당일에 계산된 행은 부등호가 strict(<) 이므로 반환되면 안 된다."""
    _insert_view_weight(engine, portfolio_id, as_of=TODAY, view_type="market", weight=0.9)

    result = get_view_weights(portfolio_id, as_of=TODAY)

    assert "market" not in result


def test_get_view_weights_returns_latest_before_as_of(engine, portfolio_id):
    """as_of 이전(strict <)에 계산된 각 관점의 최신 가중치만 반환된다 (양성 경로)."""
    _insert_view_weight(engine, portfolio_id, as_of=YESTERDAY, view_type="market", weight=0.5)
    _insert_view_weight(engine, portfolio_id, as_of=TODAY, view_type="market", weight=0.9)
    _insert_view_weight(engine, portfolio_id, as_of=YESTERDAY, view_type="sentiment", weight=0.2)

    result = get_view_weights(portfolio_id, as_of=TODAY)

    assert result == {"market": 0.5, "sentiment": 0.2}
