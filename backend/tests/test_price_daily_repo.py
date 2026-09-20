"""price_daily 조회·적재 경로 테스트. postgres 가 필요하다.

이 테이블에는 실데이터(유니버스 60종목)가 들어 있으므로 테스트는 자기가 만든
TEST_ 티커만 읽고 쓰고 지운다 — 실종목을 건드리지 않고, requires_backfill 이 필요 없다.
날짜는 실데이터가 있는 2024-01 안쪽에 둔다(하이퍼테이블에 새 청크를 만들지 않으려고).

조회 경계(as_of)와 적재 방어(NaN·중복·종가 결측)는 재현성·시점 무결성 주장의 물증이다.
"""
from __future__ import annotations

import inspect
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.repositories.price_daily import get_price_window, upsert_price_bars
from app.views.market.features import PriceBar, _bar_from_mapping, compute_features

START = date(2024, 1, 2)
ROW_KEYS = {"trade_date", "open", "high", "low", "close", "volume"}


@pytest.fixture
def ticker(engine):
    generated = f"TEST_{uuid.uuid4().hex[:8].upper()}"
    yield generated
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM price_daily WHERE ticker = :t"), {"t": generated})


def _bars(count: int, *, start: date = START) -> list[dict]:
    # 결정론적 시계열. 이진수로 정확히 표현되는 값만 써서 == 비교가 흔들리지 않는다.
    return [
        {
            "trade_date": start + timedelta(days=index),
            "open": 100.0 + index * 0.25,
            "high": 101.0 + index * 0.25,
            "low": 99.0 + index * 0.25,
            "close": 100.5 + index * 0.25,
            "volume": 1000 + index,
        }
        for index in range(count)
    ]


def _count(engine, ticker: str) -> int:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT count(*) FROM price_daily WHERE ticker = :t"), {"t": ticker}
        ).scalar_one()


# ── as_of 규약 ───────────────────────────────────────────────────────


def test_as_of_is_keyword_only_and_has_no_default() -> None:
    parameter = inspect.signature(get_price_window).parameters["as_of"]

    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty
    with pytest.raises(TypeError):
        get_price_window("069500", lookback_days=5)  # type: ignore[call-arg]


def test_window_includes_the_as_of_day_but_not_the_next_day(engine, ticker) -> None:
    bars = _bars(10)
    upsert_price_bars(ticker, bars=bars)
    as_of = bars[5]["trade_date"]

    window = get_price_window(ticker, as_of=as_of, lookback_days=100)

    assert window[-1]["trade_date"] == as_of  # 당일은 포함(<=)
    assert [row["trade_date"] for row in window] == [bar["trade_date"] for bar in bars[:6]]
    assert all(row["trade_date"] <= as_of for row in window)  # 다음 날 행은 안 나온다


def test_window_is_capped_by_lookback_and_ordered_oldest_first(engine, ticker) -> None:
    bars = _bars(30)
    upsert_price_bars(ticker, bars=bars)

    window = get_price_window(ticker, as_of=bars[-1]["trade_date"], lookback_days=10)

    dates = [row["trade_date"] for row in window]
    assert dates == [bar["trade_date"] for bar in bars[-10:]]  # 최근 10개, 오래된 것부터
    assert dates == sorted(dates)
    assert bars[-11]["trade_date"] not in dates  # lookback 을 넘는 과거는 안 나온다


def test_lookback_must_be_positive(engine, ticker) -> None:
    with pytest.raises(ValueError, match="1 이상"):
        get_price_window(ticker, as_of=START, lookback_days=0)


# ── 적재 ─────────────────────────────────────────────────────────────


def test_reingest_overwrites_the_same_day_without_adding_rows(engine, ticker) -> None:
    first = _bars(5)
    upsert_price_bars(ticker, bars=first)

    revised = [dict(bar) for bar in first]
    revised[2]["close"] = 200.0  # 거래소 정정·수정주가 반영은 정상이다
    upsert_price_bars(ticker, bars=revised)

    assert _count(engine, ticker) == 5
    window = get_price_window(ticker, as_of=first[-1]["trade_date"], lookback_days=10)
    assert window[2]["close"] == 200.0
    assert window[1]["close"] == first[1]["close"]


def test_the_same_trade_date_twice_in_one_call_is_rejected(engine, ticker) -> None:
    bars = _bars(3)
    bars.append(dict(bars[1]))

    with pytest.raises(ValueError, match="같은 거래일"):
        upsert_price_bars(ticker, bars=bars)
    assert _count(engine, ticker) == 0  # 부분 적재도 남지 않는다


@pytest.mark.parametrize("broken", [{"close": None}, {"close": "missing"}])
def test_a_bar_without_close_is_rejected(engine, ticker, broken) -> None:
    bars = _bars(3)
    if broken["close"] == "missing":
        del bars[1]["close"]
    else:
        bars[1]["close"] = None

    with pytest.raises(ValueError, match="close 가 없다"):
        upsert_price_bars(ticker, bars=bars)
    assert _count(engine, ticker) == 0  # 멀쩡한 다른 행도 같이 들어가지 않는다


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("close", float("nan")),
        ("open", float("nan")),
        ("high", float("inf")),
        ("low", float("-inf")),
        ("volume", float("nan")),
    ],
)
def test_non_finite_values_are_rejected(engine, ticker, field, value) -> None:
    # NUMERIC 은 NaN 을 받아 주므로 이 검사가 없으면 조용히 들어가 피처 계산까지 번진다.
    bars = _bars(3)
    bars[1][field] = value

    with pytest.raises(ValueError, match="유한수가 아니다"):
        upsert_price_bars(ticker, bars=bars)
    assert _count(engine, ticker) == 0


# ── features.py 와의 계약 ────────────────────────────────────────────


def test_returned_keys_are_the_ones_features_accepts(engine, ticker) -> None:
    # 두 모듈의 계약이다. 키 이름이 어긋나면 피처 계산이 조용히 깨진다.
    bars = _bars(40)
    upsert_price_bars(ticker, bars=bars)
    as_of = bars[-1]["trade_date"]

    rows = get_price_window(ticker, as_of=as_of, lookback_days=100)

    assert len(rows) == 40
    for row in rows:
        assert set(row) == ROW_KEYS
        assert isinstance(_bar_from_mapping(row), PriceBar)

    # 고가·저가·거래량이 features 가 받는 이름으로 왕복했다는 증거: 이 둘은 종가만으로는
    # 정의되지 않으므로 결측이면 OHLCV 가 안 들어간 것이다.
    features = compute_features(rows, as_of=as_of)
    assert features["atr_14_pct"] is not None
    assert features["volume_ratio_20"] is not None
    # features 는 소수 6자리로 반올림해 내보낸다.
    assert features["ret_1"] == pytest.approx(bars[-1]["close"] / bars[-2]["close"] - 1, abs=1e-6)
