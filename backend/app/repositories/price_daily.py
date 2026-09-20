"""price_daily as_of 질의와 적재. docs/db-erd.md 가 정본이다.

**append-only 가 아니다.** view_weights 와 달리 가격은 거래소 정정·수정주가 반영으로
같은 (종목, 거래일) 이 다시 채워지는 일이 정상이다. feature_store 와 같은 논리다.

**atr_14 컬럼은 이 모듈이 채우지 않는다.** app/views/market/features.py 가
atr_14_pct 를 Wilder 방식으로 계산하고 워밍업 120행이 v0.1-ta9 정의의 일부다.
DB 에도 넣으면 정의가 두 곳이 되어 반드시 갈라진다 (질의 8-b 답, 2026-09-19).
"""
from __future__ import annotations

import math
from datetime import date

from sqlalchemy import text

from app.core.db import get_engine

# 적재 대상 컬럼. atr_14 · nav 는 위 주석의 이유로 비워 둔다.
_BAR_FIELDS = ("open", "high", "low", "close", "volume")


def get_price_window(ticker: str, *, as_of: date, lookback_days: int) -> list[dict]:
    """as_of 시점까지의 최근 lookback_days 거래일. 오래된 것부터.

    반환 dict 의 키는 app/views/market/features.py 의 _bar_from_mapping 이 받는
    이름 그대로다 — compute_features 에 그대로 먹일 수 있어야 한다.

    as_of 규약: 키워드 필수, 기본값 없음, 경계는 `<=` (거래일은 장 마감 후 확정되므로
    그날 종가를 그날 판단에 쓰는 것이 맞다. macro_indicators 와 달리 공표 시차가
    없어서 released_at 조건이 없다).
    """
    if lookback_days <= 0:
        raise ValueError(f"lookback_days 는 1 이상이어야 한다: {lookback_days!r}")

    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT trade_date, open, high, low, close, volume
                  FROM (
                        SELECT trade_date, open, high, low, close, volume
                          FROM price_daily
                         WHERE ticker     = :ticker
                           AND trade_date <= :as_of
                         ORDER BY trade_date DESC
                         LIMIT :lookback_days
                       ) recent
                 ORDER BY trade_date ASC
                """
            ),
            {"ticker": ticker, "as_of": as_of, "lookback_days": lookback_days},
        ).all()

    return [
        {
            "trade_date": row.trade_date,
            "open": _optional_float(row.open),
            "high": _optional_float(row.high),
            "low": _optional_float(row.low),
            "close": _optional_float(row.close),
            "volume": _optional_float(row.volume),
        }
        for row in rows
    ]


def upsert_price_bars(ticker: str, *, bars: list[dict]) -> int:
    """일봉 여러 행을 한 트랜잭션으로 적재한다. 같은 (종목, 거래일)이면 덮어쓴다.

    한 종목 3년치가 약 740행이고 종목마다 왕복하면 적재가 분 단위로 늘어지므로
    executemany 로 묶는다. 부분 적재된 종목은 읽는 쪽에 이력이 끊긴 것처럼 보이고
    워밍업 120행 판정을 조용히 틀리게 만들기 때문에 트랜잭션도 한 개로 둔다.
    """
    if not bars:
        return 0

    params = [_validated_params(ticker, bar) for bar in bars]

    trade_dates = [param["trade_date"] for param in params]
    if len(set(trade_dates)) != len(trade_dates):
        raise ValueError(f"{ticker}: 같은 거래일이 두 번 들어왔다")

    with get_engine().begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO price_daily (ticker, trade_date, open, high, low, close, volume)
                VALUES (:ticker, :trade_date, :open, :high, :low, :close, :volume)
                ON CONFLICT (ticker, trade_date)
                DO UPDATE SET open   = EXCLUDED.open,
                              high   = EXCLUDED.high,
                              low    = EXCLUDED.low,
                              close  = EXCLUDED.close,
                              volume = EXCLUDED.volume
                """
            ),
            params,
        )
    return len(params)


def _validated_params(ticker: str, bar: dict) -> dict:
    trade_date = bar.get("trade_date")
    if not isinstance(trade_date, date):
        raise ValueError(f"{ticker}: trade_date 가 date 가 아니다: {trade_date!r}")

    params: dict = {"ticker": ticker, "trade_date": trade_date}
    for field in _BAR_FIELDS:
        params[field] = _finite_or_none(ticker, trade_date, field, bar.get(field))

    if params["close"] is None:
        # 종가 없는 행은 적재하지 않는다. 피처 계산 전부가 종가에서 나오므로
        # 이 행을 넣으면 결측이 아니라 "거래일이 있는데 값이 없는" 상태가 된다.
        raise ValueError(f"{ticker} {trade_date}: close 가 없다")

    # volume 은 BIGINT 라 소수점이 들어가면 적재 시점에 터진다.
    if params["volume"] is not None:
        params["volume"] = int(params["volume"])
    return params


def _finite_or_none(ticker: str, trade_date: date, field: str, value) -> float | None:
    # NaN/Inf 를 적재 전에 막는다. NUMERIC 은 NaN 을 **받아 주기 때문에** 더 위험하다 —
    # 터지지 않고 조용히 들어가서 피처 계산까지 전염된다. 결측은 None 이지 NaN 이 아니다.
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{ticker} {trade_date}: {field} 가 유한수가 아니다: {value!r}")
    return number


def _optional_float(value) -> float | None:
    return float(value) if value is not None else None
