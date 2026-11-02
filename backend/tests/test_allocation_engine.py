"""자산 배분형 v0.2 엔진(app/backtest/allocation_engine.py) 테스트. DB 없이 합성 가격으로 본다."""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from app.backtest import allocation_engine as alloc
from app.backtest import rules_engine as eng
from app.backtest import taxes
from app.contracts.strategy_v0_2 import StrategyV0_2

START = date(2024, 1, 1)  # 월요일


def _days(n, start=START):
    return [start + timedelta(days=i) for i in range(n)]


def _bars(closes, opens=None, start=START):
    opens = opens or list(closes)
    return [
        {"trade_date": d, "open": o, "close": c, "volume": 1000.0}
        for d, o, c in zip(_days(len(closes), start), opens, closes, strict=True)
    ]


def _spec(**extra):
    data = {
        "universe": {"tickers": ["005930", "000660"]},
        "rebalance": None,
        "backtest": {"start": "2024-01-01", "end": "2024-12-31", "capital": 1_000_000},
    }
    data.update(extra)
    return StrategyV0_2.model_validate(data)


CATS = {"005930": taxes.STOCK, "000660": taxes.STOCK}


def test_first_day_buys_target_weights_and_holds_without_rebalance():
    bars = {"005930": _bars([100] * 5), "000660": _bars([50] * 5)}
    r = alloc.run_allocation(_spec(weights={"005930": 0.6, "000660": 0.3}), bars, _days(5), CATS)
    assert [f["date"] for f in r.fills] == [START, START]
    by = {f["ticker"]: f for f in r.fills}
    unit = 100 * (1 + eng.SLIPPAGE_RATE) * (1 + eng.FEE_RATE)
    assert by["005930"]["shares"] == math.floor(600_000 / unit)
    assert by["005930"]["target_weight"] == 0.6 and by["000660"]["target_weight"] == 0.3
    # 나머지 10% 남짓은 현금
    assert r.cash[-1][1] > 99_000


def test_monthly_rebalance_uses_previous_close_and_trades_at_open():
    n = 40
    up = [100 * (1.02**i) for i in range(n)]
    bars = {"005930": _bars(up), "000660": _bars([100] * n)}
    r = alloc.run_allocation(_spec(rebalance="RB_MONTHLY_FIRST"), bars, _days(n), CATS)
    feb1 = date(2024, 2, 1)
    feb = [f for f in r.fills if f["date"] == feb1]
    # 오른 종목을 팔고 그대로인 종목을 산다. 팔기가 먼저다
    assert [(f["ticker"], f["side"]) for f in feb] == [("005930", "sell"), ("000660", "buy")]
    assert all(f["target_weight"] == 0.5 for f in feb)


def test_rebalance_days():
    cal = (
        [date(2024, 1, d) for d in (2, 3, 15, 16, 31)]
        + [date(2024, 2, d) for d in (1, 14, 16)]
        + [date(2024, 4, 1), date(2024, 4, 2)]
    )
    assert alloc.rebalance_days("RB_MONTHLY_FIRST", cal) == [
        date(2024, 1, 2),
        date(2024, 2, 1),
        date(2024, 4, 1),
    ]
    assert alloc.rebalance_days("RB_MONTHLY_MID", cal) == [date(2024, 1, 15), date(2024, 2, 16)]
    assert alloc.rebalance_days("RB_QUARTERLY", cal) == [date(2024, 1, 2), date(2024, 4, 1)]
    assert alloc.rebalance_days("RB_WEEKLY", cal)[0] == date(2024, 1, 3)  # 그 주 마지막 거래일
    assert alloc.rebalance_days(None, cal) == []


def test_threshold_rebalances_the_next_open_after_drift():
    n = 10
    jump = [100, 100, 100, 140, 140, 140, 140, 140, 140, 140]
    bars = {"005930": _bars(jump), "000660": _bars([100] * n)}
    r = alloc.run_allocation(_spec(rebalance="RB_THRESHOLD_5"), bars, _days(n), CATS)
    # 4일째(인덱스 3) 종가에 벌어져 5일째 시가에 맞춘다
    assert sorted({f["date"] for f in r.fills}) == [START, START + timedelta(days=4)]


def test_other_etf_gain_tax_uses_average_cost():
    friday = date(2024, 1, 5)
    bars = {"005930": _bars([100, 100, 200, 200], start=friday), "000660": _bars([100] * 4, start=friday)}
    cats = {"005930": taxes.ETF_OTHER, "000660": taxes.STOCK}
    r = alloc.run_allocation(_spec(rebalance="RB_WEEKLY"), bars, _days(4, friday), cats)
    sell = next(f for f in r.fills if f["side"] == "sell" and f["ticker"] == "005930")
    buy = next(f for f in r.fills if f["side"] == "buy" and f["ticker"] == "005930")
    assert sell["tax"] == pytest.approx((sell["price"] - buy["price"]) * sell["shares"] * 0.154)


def test_rules_strategy_is_not_this_engine():
    spec = StrategyV0_2.model_validate(
        {
            "universe": {"tickers": ["005930"]},
            "rules": [
                {
                    "target": "each",
                    "entry": {"left": {"indicator": "close", "of": "self"}, "op": "<", "right": {"value": 1}},
                }
            ],
        }
    )
    with pytest.raises(eng.UnsupportedStrategy):
        alloc.run_allocation(spec, {"005930": _bars([1, 2])}, _days(2), {"005930": taxes.STOCK})


def test_same_input_same_result():
    bars = {"005930": _bars([100 + i for i in range(30)]), "000660": _bars([100 - i for i in range(30)])}
    a = alloc.run_allocation(_spec(rebalance="RB_WEEKLY"), bars, _days(30), CATS)
    assert a == alloc.run_allocation(_spec(rebalance="RB_WEEKLY"), bars, _days(30), CATS)
