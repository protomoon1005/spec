"""조건형 백테스트 엔진(app/backtest/rules_engine.py) 테스트. DB 없이 합성 가격으로 본다."""
from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from app.backtest import rules_engine as eng
from app.backtest import taxes
from app.contracts.strategy_v0_2 import StrategyV0_2

START = date(2024, 1, 1)


def _bars(closes, opens=None, volumes=None, start=START):
    opens = opens or list(closes)
    volumes = volumes or [1000.0] * len(closes)
    return [
        {"trade_date": start + timedelta(days=i), "open": o, "close": c, "volume": v}
        for i, (o, c, v) in enumerate(zip(opens, closes, volumes, strict=True))
    ]


def _calendar(n, start=START):
    return [start + timedelta(days=i) for i in range(n)]


def _spec(entry, exit_, tickers=("005930",), **extra):
    data = {
        "universe": {"tickers": list(tickers)},
        "rules": [{"target": "each", "entry": entry, "exit": exit_}],
        "backtest": {"start": "2024-01-01", "end": "2024-12-31", "capital": 1_000_000},
    }
    data.update(extra)
    return StrategyV0_2.model_validate(data)


def close_vs(op, value):
    return {"left": {"indicator": "close", "of": "self"}, "op": op, "right": {"value": value}}


def held(indicator, op, value):
    return {"left": {"indicator": indicator, "of": "self"}, "op": op, "right": {"value": value}}


STOCK = {"005930": taxes.STOCK}


def test_decide_on_close_fill_next_open_with_costs():
    closes = [100, 100, 90, 95, 120, 130]
    opens = [100, 100, 100, 92, 118, 131]
    spec = _spec(close_vs("cross_below", 95), close_vs(">", 110))
    r = eng.run_rules(spec, {"005930": _bars(closes, opens)}, _calendar(6), STOCK)

    buy, sell = r.fills
    # 2일째(종가 90) 떨어지는 순간 판단, 3일째 시가 92 에 체결
    assert buy["decided_on"] == START + timedelta(days=2)
    assert buy["date"] == START + timedelta(days=3)
    price = 92 * (1 + eng.SLIPPAGE_RATE)
    shares = math.floor(1_000_000 / (price * (1 + eng.FEE_RATE)))
    assert buy["price"] == pytest.approx(price)
    assert buy["shares"] == shares
    # 4일째 종가 120 > 110 판단, 5일째 시가 131 에 매도. 2024년 주식 거래세 0.18%
    sell_price = 131 * (1 - eng.SLIPPAGE_RATE)
    assert sell["date"] == START + timedelta(days=5)
    assert sell["tax"] == pytest.approx(shares * sell_price * 0.0018)
    assert r.trades[0]["holding_days"] == 2
    assert r.holdings_at_end == {}


def test_sell_first_when_both_conditions_are_true():
    spec = _spec(close_vs("<", 95), close_vs("<", 99))
    r = eng.run_rules(spec, {"005930": _bars([100, 90, 90, 90])}, _calendar(4), STOCK)
    assert r.fills == []
    assert len(r.same_day_conflicts) == 3


def test_halt_day_defers_the_order():
    closes = [100, 90, 90, 90, 90]
    opens = [100, 100, None, None, 91]  # 2 · 3일째 거래정지
    spec = _spec(close_vs("cross_below", 95), None)
    r = eng.run_rules(spec, {"005930": _bars(closes, opens)}, _calendar(5), STOCK)
    assert [f["date"] for f in r.fills] == [START + timedelta(days=4)]
    assert r.fills[0]["decided_on"] == START + timedelta(days=1)


def test_signal_on_the_last_day_is_not_filled():
    spec = _spec(close_vs("cross_below", 95), None)
    r = eng.run_rules(spec, {"005930": _bars([100, 100, 90])}, _calendar(3), STOCK)
    assert r.fills == []
    assert r.unfilled[0]["reason"] == "기간이 끝나 체결하지 않음"


def test_holding_days_counts_the_fill_day_as_zero():
    spec = _spec(close_vs("cross_below", 95), held("holding_days", ">=", 2))
    r = eng.run_rules(spec, {"005930": _bars([100, 90, 90, 90, 90, 90, 90])}, _calendar(7), STOCK)
    buy, sell = r.fills
    # 2일째 체결(0일), 4일째 종가에 2일이 되어 판단, 5일째 매도
    assert buy["date"] == START + timedelta(days=2)
    assert sell["decided_on"] == START + timedelta(days=4)
    assert sell["date"] == START + timedelta(days=5)


def test_stop_loss_uses_the_buy_fill_price():
    closes = [100, 90, 90, 85, 80, 80]
    spec = _spec(close_vs("cross_below", 95), held("entry_return", "<=", -0.10))
    r = eng.run_rules(spec, {"005930": _bars(closes)}, _calendar(6), STOCK)
    buy, sell = r.fills
    assert buy["price"] == pytest.approx(90 * (1 + eng.SLIPPAGE_RATE))
    # 80 / 90.045 - 1 = -11.2% 가 처음 -10% 아래로 간 날(4일째) 판단
    assert sell["decided_on"] == START + timedelta(days=4)


def test_slots_split_capital_and_respect_the_cap():
    bars = {"005930": _bars([100, 90, 90, 90]), "000660": _bars([100, 90, 90, 90])}
    spec = _spec(close_vs("cross_below", 95), None, tickers=("005930", "000660"))
    categories = {**STOCK, "000660": taxes.STOCK}
    r = eng.run_rules(spec, bars, _calendar(4), categories)
    spent = {f["ticker"]: f["shares"] * f["price"] for f in r.fills}
    assert set(spent) == {"005930", "000660"}
    assert all(v <= 500_000 for v in spent.values())

    capped = _spec(close_vs("cross_below", 95), None, constraint={"max_weight_per_asset": 0.3})
    r = eng.run_rules(capped, {"005930": _bars([100, 90, 90, 90])}, _calendar(4), STOCK)
    assert len(r.fills) == 1
    assert 290_000 < r.fills[0]["shares"] * r.fills[0]["price"] <= 300_000


def test_other_etf_pays_gain_tax_and_domestic_equity_etf_pays_none():
    closes = [100, 90, 90, 130, 130]
    spec = _spec(close_vs("cross_below", 95), close_vs(">", 120))
    other = eng.run_rules(spec, {"005930": _bars(closes)}, _calendar(5), {"005930": taxes.ETF_OTHER})
    sell = other.fills[1]
    buy = other.fills[0]
    assert sell["tax"] == pytest.approx((sell["price"] - buy["price"]) * sell["shares"] * 0.154)
    domestic = eng.run_rules(spec, {"005930": _bars(closes)}, _calendar(5),
                             {"005930": taxes.ETF_DOMESTIC_EQUITY})
    assert domestic.fills[1]["tax"] == 0.0


def test_future_prices_do_not_change_past_decisions():
    closes = [100 + 10 * math.sin(i / 4) for i in range(80)]
    spec = _spec(
        {"left": {"indicator": "rsi", "window": 14, "of": "self"},
         "op": "cross_below", "right": {"value": 45}},
        {"left": {"indicator": "close", "of": "self"}, "op": "cross_above",
         "right": {"indicator": "ma", "window": 5, "of": "self"}},
    )
    base = eng.run_rules(spec, {"005930": _bars(closes)}, _calendar(80), STOCK)
    cut = 50
    changed = closes[:cut] + [c * 2 for c in closes[cut:]]
    other = eng.run_rules(spec, {"005930": _bars(changed)}, _calendar(80), STOCK)
    day = START + timedelta(days=cut - 1)
    assert [f for f in base.fills if f["date"] <= day] == [f for f in other.fills if f["date"] <= day]
    assert base.equity[:cut] == other.equity[:cut]
    assert base.fills, "시험 데이터에서 거래가 한 번은 나와야 의미가 있다"


def test_same_input_same_result():
    spec = _spec(close_vs("cross_below", 95), close_vs(">", 110))
    bars = {"005930": _bars([100, 90, 95, 120, 100, 90, 115])}
    assert eng.run_rules(spec, bars, _calendar(7), STOCK) == eng.run_rules(spec, bars, _calendar(7), STOCK)


@pytest.mark.parametrize("subject", ["KOSPI200", "VIX"])
def test_index_and_vix_conditions_are_outside_the_demo(subject):
    indicator = "vix" if subject == "VIX" else "close"
    entry = {"left": {"indicator": indicator, "of": subject}, "op": "<", "right": {"value": 1}}
    with pytest.raises(eng.UnsupportedStrategy):
        eng.run_rules(_spec(entry, None), {"005930": _bars([1, 2])}, _calendar(2), STOCK)


def test_allocation_only_strategy_is_not_this_engine():
    spec = StrategyV0_2.model_validate({"universe": {"tickers": ["005930"]}})
    with pytest.raises(eng.UnsupportedStrategy):
        eng.run_rules(spec, {"005930": _bars([1, 2])}, _calendar(2), STOCK)


def test_buy_and_hold_and_summary():
    equity = eng.run_buy_and_hold({"005930": 1.0}, {"005930": _bars([100, 110], [100, 105])}, _calendar(2),
                                  1_000_000)
    unit = 100 * (1 + eng.SLIPPAGE_RATE) * (1 + eng.FEE_RATE)
    shares = math.floor(1_000_000 / unit)
    assert equity[1][1] == pytest.approx(1_000_000 - shares * unit + shares * 110)
    days = _calendar(3)
    summary = eng.summarize([(days[0], 100.0), (days[1], 80.0), (days[2], 120.0)])
    assert summary["total_return"] == pytest.approx(0.2)
    assert summary["mdd"] == pytest.approx(-0.2)
