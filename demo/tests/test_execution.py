"""v0.2 엔진: 체결 시점 · 1/N 자리 · 팔기 우선 · entry_return/holding_days · 다른 대상(of) ·
배분만 전략 리밸런싱 · 비교 기준 · 비용 · 재현성."""
import pandas as pd
import pytest

from conftest import STOCK, cmp, ind, make_ohlcv, strategy_dict, wavy_ohlcv
from core.backtest import run_backtest
from core.schema import Strategy

NO_COST = {"commission_rate": 0.0, "slippage_rate": 0.0}
NEVER_EXIT = cmp(ind("holding_days"), ">=", 100_000)


def _run(d, data, instruments=None):
    instruments = instruments or {t: STOCK for t in data if t not in ("KS11", "KS200", "KQ11", "VIX")}
    return run_backtest(Strategy.model_validate(d), data=data, instruments=instruments)


def _dates(df):
    return list(df.index.strftime("%Y-%m-%d"))


def _fills(res, ticker=None):
    return [f for f in res.fills if ticker is None or f["ticker"] == ticker]


# ---------------------------------------------------------------- 기본값

def test_defaults_follow_v02():
    s = Strategy.model_validate({"assets": [{"ticker": "005930", "name": "삼성전자"}],
                                 "rules": {"entry": RSI(), "exit": MA5()}})
    b = s.backtest
    assert (b.start, b.end, b.initial_cash) == ("2023-01-01", "2025-12-31", 10_000_000)
    assert b.commission_rate == pytest.approx(0.00015)   # 0.015%
    assert b.slippage_rate == pytest.approx(0.0005)      # 0.05%
    assert s.rules.target == "each"
    assert s.assets[0].weight is None                    # None = 1/N 자리


def RSI():
    return cmp(ind("rsi", 14), "cross_below", 30)


def MA5():
    return cmp(ind("close"), "cross_below", ind("ma", 5))


# ---------------------------------------------------------------- 체결 시점

CLOSES = [110, 105, 99, 101, 108, 115, 121, 118, 117, 116]
OPENS = [110, 109, 104, 97, 102, 107, 116, 125, 117, 116]
SIMPLE_RULES = {"target": "each",
                "entry": cmp(ind("close"), "cross_below", 100),
                "exit": cmp(ind("close"), "cross_above", 120)}


def test_fill_next_day_open_with_slippage():
    df = make_ohlcv(CLOSES, opens=OPENS)
    res = _run(strategy_dict(rules=SIMPLE_RULES, backtest={"initial_cash": 1_000_000}), {"TEST01": df})
    d = _dates(df)
    buy, sell = _fills(res)
    assert (buy["side"], buy["signal_date"], buy["date"]) == ("buy", d[2], d[3])
    assert buy["price"] == pytest.approx(97 * 1.0005)          # 다음 날 시가 + 체결 오차
    assert (sell["side"], sell["signal_date"], sell["date"]) == ("sell", d[6], d[7])
    assert sell["price"] == pytest.approx(125 * 0.9995)
    assert len(res.trades) == 1 and res.trades[0]["exit_date"] == d[7]


def test_signal_on_last_day_is_not_filled():
    df = make_ohlcv([110, 105, 103, 102, 99])
    res = _run(strategy_dict(rules=SIMPLE_RULES), {"TEST01": df})
    assert res.fills == []
    assert res.unfilled_signals == [{"ticker": "TEST01", "side": "buy", "signal_date": _dates(df)[-1]}]


# ---------------------------------------------------------------- 1/N 자리

def _cross_df(closes, opens):
    return make_ohlcv(closes, opens=opens)


def test_one_of_three_meets_condition_buys_only_its_third():
    """3종목 중 1개만 조건 충족 → 그 종목만 1/3 만큼 사고 나머지는 현금."""
    a = _cross_df([105, 99, 100, 101, 102], [105, 105, 100, 100, 101])     # d1 하향 돌파 → d2 시가 100
    flat = _cross_df([105] * 5, [105] * 5)
    d = strategy_dict(
        assets=[{"ticker": t, "name": t, "weight": None} for t in ("A", "B", "C")],
        rules={"target": "each", "entry": cmp(ind("close"), "cross_below", 100), "exit": NEVER_EXIT},
        backtest={"initial_cash": 3_000_000, **NO_COST},
    )
    res = _run(d, {"A": a, "B": flat, "C": flat.copy()})
    fills = _fills(res)
    assert [(f["ticker"], f["side"], f["shares"]) for f in fills] == [("A", "buy", 10_000)]   # 1,000,000 / 100
    assert res.cash.iloc[-1] == pytest.approx(2_000_000)
    assert res.equity.iloc[-1] == pytest.approx(2_000_000 + 10_000 * 102)


def test_new_buy_does_not_sell_existing_and_is_capped_by_cash():
    """자리 = 체결일 시가 기준 총자산 × 1/N. 새 종목을 살 때 기존 종목은 팔지 않고, 현금이 모자라면 현금만큼만."""
    n = 10
    a = make_ohlcv([105, 99, 150] + [200] * 7, opens=[105, 105, 100] + [200] * 7)
    b = make_ohlcv([105, 105, 105, 99] + [100] * 6, opens=[105] * 4 + [100] * 6)
    c = make_ohlcv([105] * 5 + [99] + [100] * 4, opens=[105] * 6 + [100] * 4)
    assert len(a) == len(b) == len(c) == n
    d = strategy_dict(
        assets=[{"ticker": t, "name": t, "weight": None} for t in ("A", "B", "C")],
        rules={"target": "each", "entry": cmp(ind("close"), "cross_below", 100), "exit": NEVER_EXIT},
        backtest={"initial_cash": 3_000_000, **NO_COST},
    )
    res = _run(d, {"A": a, "B": b, "C": c})
    got = [(f["ticker"], f["side"], f["shares"]) for f in res.fills]
    # A: 3,000,000/3 → 10,000주 (현금 2,000,000)
    # B: 시가 기준 총자산 2,000,000 + 10,000×200 = 4,000,000 → 1/3 = 1,333,333 → 13,333주 (현금 666,700)
    # C: 총자산 4,000,000 → 1/3 = 1,333,333 이지만 현금 666,700 뿐 → 6,667주. A·B 는 팔지 않는다
    assert got == [("A", "buy", 10_000), ("B", "buy", 13_333), ("C", "buy", 6_667)]
    assert res.cash.iloc[-1] == pytest.approx(0.0)


def test_max_weight_caps_slot_and_leaves_cash():
    df = _cross_df([105, 99, 100, 101], [105, 105, 100, 100])
    d = strategy_dict(max_weight=0.3,
                      rules={"target": "each", "entry": cmp(ind("close"), "cross_below", 100), "exit": NEVER_EXIT},
                      backtest={"initial_cash": 1_000_000, **NO_COST})
    res = _run(d, {"TEST01": df})
    assert _fills(res)[0]["shares"] == 3_000            # 1,000,000 × 0.3 / 100


def test_user_weights_are_slots():
    a = _cross_df([105, 99, 100, 101], [105, 105, 100, 100])
    d = strategy_dict(
        assets=[{"ticker": "A", "name": "A", "weight": 0.3}, {"ticker": "B", "name": "B", "weight": 0.2}],
        rules={"target": "each", "entry": cmp(ind("close"), "cross_below", 100), "exit": NEVER_EXIT},
        backtest={"initial_cash": 1_000_000, **NO_COST})
    res = _run(d, {"A": a, "B": a.copy()})
    assert {f["ticker"]: f["shares"] for f in res.fills} == {"A": 3_000, "B": 2_000}


# ---------------------------------------------------------------- 같은 날 사기·팔기 → 팔기 우선

def test_sell_wins_when_entry_and_exit_both_true():
    closes = [110, 98, 99, 94, 97, 93, 92, 92, 110]
    df = make_ohlcv(closes)
    d = strategy_dict(rules={"target": "each",
                             "entry": cmp(ind("close"), "<", 100),
                             "exit": cmp(ind("close"), "<", 95)})
    res = _run(d, {"TEST01": df})
    dt = _dates(df)
    got = [(f["side"], f["signal_date"], f["date"]) for f in res.fills]
    assert got == [
        ("buy", dt[1], dt[2]),     # 98: 사기만 참
        ("sell", dt[3], dt[4]),    # 94: 보유 중, 둘 다 참 → 판다
        ("buy", dt[4], dt[5]),     # 97: 사기만 참
        ("sell", dt[5], dt[6]),    # 93: 보유 중, 둘 다 참 → 판다
    ]                              # 92·92: 미보유, 둘 다 참 → 사지 않는다
    assert [c["date"] for c in res.conflicts] == [dt[3], dt[5], dt[6], dt[7]]


# ---------------------------------------------------------------- entry_return · holding_days

def test_entry_return_stop_loss_minus_10pct():
    closes = [105, 99, 100, 95, 90, 89, 88]
    opens = [105, 105, 100, 99, 95, 90, 88]
    df = make_ohlcv(closes, opens=opens)
    d = strategy_dict(rules={"target": "each",
                             "entry": cmp(ind("close"), "cross_below", 100),
                             "exit": cmp(ind("entry_return"), "<=", -10)},
                      backtest=NO_COST)
    res = _run(d, {"TEST01": df})
    dt = _dates(df)
    buy, sell = res.fills
    assert buy["date"] == dt[2] and buy["price"] == 100
    # 종가 95 → -5%, 90 → -10% (<= -10 참) → 다음 날 시가 90 매도
    assert (sell["signal_date"], sell["date"], sell["price"]) == (dt[4], dt[5], 90)


def test_holding_days_5_sells_after_five_trading_days():
    df = make_ohlcv([110, 99, 101, 102, 103, 104, 105, 106, 107, 108])
    d = strategy_dict(rules={"target": "each",
                             "entry": cmp(ind("close"), "cross_below", 100),
                             "exit": cmp(ind("holding_days"), ">=", 5)})
    res = _run(d, {"TEST01": df})
    dt = _dates(df)
    buy, sell = res.fills
    # d2 시가 매수 → d2 종가에 보유 0일, d7 종가에 5일 → d8 시가 매도
    assert buy["date"] == dt[2]
    assert (sell["signal_date"], sell["date"]) == (dt[7], dt[8])
    assert res.trades[0]["holding_days"] == 6     # 체결일 기준 d2 → d8


# ---------------------------------------------------------------- of: 다른 대상

def test_buy_only_when_kospi200_above_its_ma20():
    """'코스피200 이 20일선 위일 때만 산다' — 조건은 코스피200 값으로, 매매는 그 종목으로."""
    n = 45
    ks = make_ohlcv([300 - i for i in range(25)] + [276 + 3 * i for i in range(1, n - 24)])
    asset = make_ohlcv([1000 + (i % 3) for i in range(n)])
    d = strategy_dict(rules={"target": "each",
                             "entry": {"all": [cmp(ind("close", of="KOSPI200"), ">", ind("ma", 20, of="KOSPI200")),
                                               cmp(ind("close"), ">", 0)]},
                             "exit": NEVER_EXIT})
    res = _run(d, {"TEST01": asset, "KS200": ks})
    above = ks["Close"] > ks["Close"].rolling(20).mean()
    first = above[above].index[0].strftime("%Y-%m-%d")
    assert res.fills[0]["signal_date"] == first
    assert first > ks.index[24].strftime("%Y-%m-%d")      # 하락 구간에서는 사지 않았다


def test_other_ticker_as_condition_source():
    """다른 종목의 값으로 조건: B 종가가 100 아래로 내려가면 A 를 산다."""
    a = make_ohlcv([500] * 6)
    b = make_ohlcv([105, 104, 99, 98, 97, 96])
    d = strategy_dict(assets=[{"ticker": "A", "name": "A", "weight": None}],
                      rules={"target": "each",
                             "entry": cmp(ind("close", of="B"), "cross_below", 100),
                             "exit": NEVER_EXIT})
    res = _run(d, {"A": a, "B": b}, instruments={"A": STOCK})
    assert [(f["ticker"], f["signal_date"]) for f in res.fills] == [("A", _dates(a)[2])]


# ---------------------------------------------------------------- 배분만 (조건 없음)

def test_allocation_only_rebalances_on_first_trading_day_of_month():
    idx = pd.bdate_range("2024-01-02", "2024-03-08", name="Date")
    a = make_ohlcv([100 + 2 * i for i in range(len(idx))], index=idx)        # 꾸준히 오름
    b = make_ohlcv([100.0] * len(idx), index=idx)                              # 그대로
    d = strategy_dict(assets=[{"ticker": "A", "name": "A", "weight": None},
                              {"ticker": "B", "name": "B", "weight": None}],
                      rules=None, rebalance="monthly_first",
                      backtest={"initial_cash": 1_000_000, **NO_COST})
    res = _run(d, {"A": a, "B": b})
    by_date = {}
    for f in res.fills:
        by_date.setdefault(f["date"], []).append((f["ticker"], f["side"], f["reason"]))
    # 첫날 시가에 비중대로 매수, 이후 매달 첫 거래일(2/1, 3/1) 종가 판단 → 다음 거래일 시가 체결
    assert list(by_date) == ["2024-01-02", "2024-02-02", "2024-03-04"]
    assert sorted(by_date["2024-01-02"]) == [("A", "buy", "initial"), ("B", "buy", "initial")]
    assert sorted(by_date["2024-02-02"]) == [("A", "sell", "rebalance"), ("B", "buy", "rebalance")]
    assert {f["signal_date"] for f in res.fills if f["reason"] == "rebalance"} == {"2024-02-01", "2024-03-01"}
    # 손계산 (2/2 시가 A 144, B 100, 첫날 각 5,000주, 현금 0):
    #   시가 기준 총자산 5,000×144 + 5,000×100 = 1,220,000 → 자리 610,000씩
    #   A 넘침 720,000 - 610,000 = 110,000 → floor(110,000/144) = 763주 매도 → 109,872원, 거래세 2024 0.18% = 197.7696
    #   B 모자람 110,000 이지만 현금 109,674.2304 → floor(/100) = 1,096주 매수
    feb = [f for f in res.fills if f["date"] == "2024-02-02"]
    assert [(f["ticker"], f["shares"]) for f in feb] == [("A", 763), ("B", 1_096)]
    assert feb[0]["tax"] == pytest.approx(763 * 144 * 0.0018)
    assert res.positions.loc["2024-02-02"].tolist() == [5_000 - 763, 5_000 + 1_096]


def test_allocation_only_requires_rebalance_period():
    df = make_ohlcv([100] * 5)
    with pytest.raises(ValueError):
        _run(strategy_dict(rules=None, rebalance=None), {"TEST01": df})


# ---------------------------------------------------------------- 비교 기준

def test_benchmarks_kospi_index_and_buy_and_hold():
    df = make_ohlcv(CLOSES, opens=OPENS)
    ks11 = make_ohlcv([2500 + 10 * i for i in range(len(CLOSES))], opens=[2490 + 10 * i for i in range(len(CLOSES))])
    res = _run(strategy_dict(rules=SIMPLE_RULES, backtest={"initial_cash": 1_000_000, **NO_COST}),
               {"TEST01": df, "KS11": ks11})
    kospi = res.benchmarks["kospi"]
    assert kospi.iloc[0] == pytest.approx(1_000_000 * ks11["Close"].iloc[0] / ks11["Open"].iloc[0])
    assert kospi.iloc[-1] == pytest.approx(1_000_000 * ks11["Close"].iloc[-1] / ks11["Open"].iloc[0])
    bh = res.benchmarks["buy_hold"]
    shares = 1_000_000 // OPENS[0]
    assert bh.iloc[-1] == pytest.approx(1_000_000 - shares * OPENS[0] + shares * CLOSES[-1])
    assert res.metrics["kospi_total_return"] == pytest.approx(kospi.iloc[-1] / 1_000_000 - 1)
    assert res.metrics["buy_hold_total_return"] == pytest.approx(bh.iloc[-1] / 1_000_000 - 1)


def test_kospi_benchmark_missing_is_noted_not_invented():
    df = make_ohlcv(CLOSES, opens=OPENS)
    res = _run(strategy_dict(rules=SIMPLE_RULES), {"TEST01": df})
    assert res.benchmarks["kospi"] is None
    assert res.metrics["kospi_total_return"] is None
    assert any("코스피" in n for n in res.notes)


# ---------------------------------------------------------------- 비용 (손계산 대조)
# 자금 1,000,000 / 수수료 0.015% / 체결 오차 0.05% / 매수 시가 10,000 / 매도 시가 11,000 (2024년)
#   매수 체결가 10,000 × 1.0005 = 10,005 → 수량 floor(1,000,000 / (10,005 × 1.00015)) = 99
#   매수금액 990,495, 수수료 148.57425
#   매도 체결가 11,000 × 0.9995 = 10,994.5 → 매도금액 1,088,455.5, 수수료 163.268325
#   세금: 코스피 주식 2024 = 0.18% → 1,959.2199 / 국내 주식형 ETF 0 / 그 밖의 ETF 차익 (10,994.5-10,005)×99×15.4% = 15,085.917

COST_CLOSES = [10_500, 9_900, 10_200, 12_100, 12_000]
COST_OPENS = [10_500, 10_400, 10_000, 10_300, 11_000]


@pytest.mark.parametrize("category,tax", [
    ("KOSPI_STOCK", 1_959.2199),
    ("ETF_DOMESTIC_EQUITY", 0.0),
    ("ETF_OTHER", 15_085.917),
])
def test_end_to_end_costs(category, tax):
    df = make_ohlcv(COST_CLOSES, opens=COST_OPENS)
    d = strategy_dict(rules={"target": "each",
                             "entry": cmp(ind("close"), "cross_below", 10_000),
                             "exit": cmp(ind("close"), "cross_above", 12_000)},
                      backtest={"initial_cash": 1_000_000})
    res = _run(d, {"TEST01": df}, instruments={"TEST01": category})
    buy, sell = res.fills
    assert (buy["shares"], buy["price"]) == (99, pytest.approx(10_005))
    assert buy["commission"] == pytest.approx(148.57425)
    assert sell["price"] == pytest.approx(10_994.5)
    assert sell["commission"] == pytest.approx(163.268325)
    assert sell["tax"] == pytest.approx(tax)
    final = 1_000_000 - 990_495 - 148.57425 + 1_088_455.5 - 163.268325 - tax
    assert res.equity.iloc[-1] == pytest.approx(final)
    assert res.trades[0]["pnl"] == pytest.approx(final - 1_000_000)


def test_unknown_ticker_without_tax_category_is_refused():
    df = make_ohlcv(COST_CLOSES, opens=COST_OPENS)
    with pytest.raises(ValueError):
        run_backtest(Strategy.model_validate(strategy_dict()), data={"TEST01": df})


def test_tickers_json_gives_tax_category():
    """instruments 를 주지 않으면 data/tickers.json 의 market·kind·tax_type 으로 세율 분류를 정한다."""
    df = make_ohlcv(COST_CLOSES, opens=COST_OPENS)
    d = strategy_dict(assets=[{"ticker": "005930", "name": "삼성전자", "weight": None}],
                      rules={"target": "each",
                             "entry": cmp(ind("close"), "cross_below", 10_000),
                             "exit": cmp(ind("close"), "cross_above", 12_000)},
                      backtest={"initial_cash": 1_000_000})
    res = run_backtest(Strategy.model_validate(d), data={"005930": df})
    assert res.fills[1]["tax"] == pytest.approx(1_959.2199)


# ---------------------------------------------------------------- 재현성

def test_reproducible_fills_and_hash():
    d = strategy_dict(rules={"target": "each", "entry": RSI(),
                             "exit": {"any": [MA5(), cmp(ind("entry_return"), "<=", -7),
                                              cmp(ind("holding_days"), ">=", 30)]}})
    df = wavy_ohlcv()
    r1 = _run(d, {"TEST01": df.copy()})
    r2 = _run(d, {"TEST01": df.copy()})
    assert len(r1.fills) > 2
    assert r1.fills == r2.fills and r1.trades == r2.trades
    assert r1.result_hash == r2.result_hash and r1.spec_hash == r2.spec_hash
    assert r1.data_snapshot == r2.data_snapshot


def test_hash_changes_when_spec_changes():
    df = wavy_ohlcv()
    a = _run(strategy_dict(), {"TEST01": df})
    b = _run(strategy_dict(backtest={"commission_rate": 0.003}), {"TEST01": df})
    assert a.spec_hash != b.spec_hash and a.result_hash != b.result_hash
