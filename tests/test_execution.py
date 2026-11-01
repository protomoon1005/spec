"""2. 체결 시점 / 3. 재현성 / 4. 비용."""
import pytest

from conftest import base_spec_dict, make_ohlcv, wavy_ohlcv
from core.backtest import buy_fill, run_backtest, sell_fill
from core.schema import DemoSpec

# 종가 100 하향 돌파 → 매수, 종가 120 상향 돌파 → 매도. 시가는 종가와 다르게 둔다.
CLOSES = [110, 105, 99, 101, 108, 115, 121, 118, 117, 116]
OPENS = [110, 109, 104, 97, 102, 107, 116, 125, 117, 116]
#                    ^T=2 신호   ^T+1 시가 97      ^T=6 신호  ^T+1 시가 125

SIMPLE = base_spec_dict(
    entry={"logic": "all", "conditions": [{"left": "CLOSE", "op": "cross_below", "right": 100}]},
    exit={
        "logic": "any",
        "conditions": [{"left": "CLOSE", "op": "cross_above", "right": 120}],
        "stop_loss": None, "take_profit": None, "max_hold_days": None,
    },
    backtest={"start": "2000-01-01", "end": "2100-12-31", "initial_cash": 1_000_000},
)


def test_fill_next_day_open():
    df = make_ohlcv(CLOSES, opens=OPENS)
    res = run_backtest(DemoSpec.model_validate(SIMPLE), data={"TEST01": df})
    assert len(res.trades) == 1
    tr = res.trades[0]
    d = df.index.strftime("%Y-%m-%d")
    assert tr["entry_signal_date"] == d[2]
    assert tr["entry_date"] == d[3]
    assert tr["entry_price"] == 97.0          # T+1 시가 (종가 101 아님)
    assert tr["exit_signal_date"] == d[6]
    assert tr["exit_date"] == d[7]
    assert tr["exit_price"] == 125.0          # T+1 시가 (종가 118 아님)


def test_signal_on_last_day_is_not_filled():
    closes = [110, 105, 103, 102, 99]
    df = make_ohlcv(closes)
    res = run_backtest(DemoSpec.model_validate(SIMPLE), data={"TEST01": df})
    assert res.trades == []
    assert res.unfilled_signals == [{"ticker": "TEST01", "side": "buy", "signal_date": df.index[-1].strftime("%Y-%m-%d")}]


def test_stop_loss_uses_close_then_next_open():
    spec = base_spec_dict(
        entry={"logic": "all", "conditions": [{"left": "CLOSE", "op": "cross_below", "right": 100}]},
        exit={"logic": "any", "conditions": [], "stop_loss": 0.05, "take_profit": None, "max_hold_days": None},
    )
    closes = [110, 99, 100, 96, 94, 93]   # 매수가 100(T+1 시가) → 종가 94 에서 -6% → 다음날 시가 매도
    opens = [110, 105, 100, 99, 95, 92]
    df = make_ohlcv(closes, opens=opens)
    res = run_backtest(DemoSpec.model_validate(spec), data={"TEST01": df})
    tr = res.trades[0]
    d = df.index.strftime("%Y-%m-%d")
    assert tr["entry_price"] == 100.0
    assert tr["exit_signal_date"] == d[4]
    assert tr["exit_date"] == d[5] and tr["exit_price"] == 92.0
    assert tr["exit_reason"] == "stop_loss"


def test_max_hold_days():
    spec = base_spec_dict(
        entry={"logic": "all", "conditions": [{"left": "CLOSE", "op": "cross_below", "right": 100}]},
        exit={"logic": "any", "conditions": [], "stop_loss": None, "take_profit": None, "max_hold_days": 3},
    )
    df = make_ohlcv([110, 99, 101, 102, 103, 104, 105])
    res = run_backtest(DemoSpec.model_validate(spec), data={"TEST01": df})
    d = df.index.strftime("%Y-%m-%d")
    tr = res.trades[0]
    # 진입 d[2] 시가, 보유 d2,d3,d4 → d4 종가에 3일 도달 → d5 시가 매도
    assert tr["entry_date"] == d[2] and tr["exit_date"] == d[5]
    assert tr["exit_reason"] == "max_hold_days"


# ---------- 3. 재현성 ----------

def test_reproducible_trades_and_hash():
    spec_d = base_spec_dict(
        exit={"logic": "any", "conditions": [{"left": "CLOSE", "op": "cross_below", "right": "SMA_5"}],
              "stop_loss": 0.07, "take_profit": 0.15, "max_hold_days": 30},
    )
    df = wavy_ohlcv()
    r1 = run_backtest(DemoSpec.model_validate(spec_d), data={"TEST01": df.copy()})
    r2 = run_backtest(DemoSpec.model_validate(spec_d), data={"TEST01": df.copy()})
    assert len(r1.trades) > 0
    assert r1.trades == r2.trades
    assert r1.result_hash == r2.result_hash
    assert r1.spec_hash == r2.spec_hash
    assert r1.data_snapshot == r2.data_snapshot


def test_hash_changes_when_spec_changes():
    df = wavy_ohlcv()
    a = run_backtest(DemoSpec.model_validate(base_spec_dict()), data={"TEST01": df})
    b_d = base_spec_dict()
    b_d["costs"]["commission_bps"] = 30
    b = run_backtest(DemoSpec.model_validate(b_d), data={"TEST01": df})
    assert a.spec_hash != b.spec_hash
    assert a.result_hash != b.result_hash


# ---------- 4. 비용 (손계산 대조) ----------
# 자금 1,000,000 / 수수료 1.5bps = 0.015% / 매수 시가 10,000 / 매도 시가 11,000
#   수량 = floor(1,000,000 / (10,000 × 1.00015)) = floor(99.985...) = 99
#   매수금액 990,000, 매수수수료 990,000 × 0.00015 = 148.5 → 매입원가 990,148.5, 잔액 9,851.5
#   매도금액 1,089,000, 매도수수료 1,089,000 × 0.00015 = 163.35
#   주식 세금 0.20% = 2,178 → 순매도대금 1,086,658.65 → 최종 1,096,510.15 / 손익 96,510.15
#   ETF  세금 0     = 0     → 순매도대금 1,088,836.65 → 최종 1,098,688.15 / 손익 98,688.15

def test_buy_sell_fill_math():
    shares, gross, comm = buy_fill(cash=1_000_000, price=10_000, commission_bps=1.5)
    assert (shares, gross, comm) == (99, 990_000, pytest.approx(148.5))
    gross, comm, tax = sell_fill(shares=99, price=11_000, commission_bps=1.5, tax_rate=0.002)
    assert gross == 1_089_000
    assert comm == pytest.approx(163.35)
    assert tax == pytest.approx(2_178)


@pytest.mark.parametrize("asset_type,final,pnl,tax", [
    ("stock", 1_096_510.15, 96_510.15, 2_178.0),
    ("etf", 1_098_688.15, 98_688.15, 0.0),
])
def test_end_to_end_costs(asset_type, final, pnl, tax):
    closes = [10_500, 9_900, 10_200, 12_100, 12_000]
    opens = [10_500, 10_400, 10_000, 10_300, 11_000]
    df = make_ohlcv(closes, opens=opens)
    spec = base_spec_dict(
        entry={"logic": "all", "conditions": [{"left": "CLOSE", "op": "cross_below", "right": 10_000}]},
        exit={"logic": "any", "conditions": [{"left": "CLOSE", "op": "cross_above", "right": 12_000}],
              "stop_loss": None, "take_profit": None, "max_hold_days": None},
        backtest={"start": "2000-01-01", "end": "2100-12-31", "initial_cash": 1_000_000},
    )
    res = run_backtest(DemoSpec.model_validate(spec), data={"TEST01": df}, asset_types={"TEST01": asset_type})
    tr = res.trades[0]
    assert tr["entry_price"] == 10_000 and tr["exit_price"] == 11_000 and tr["shares"] == 99
    assert tr["entry_commission"] == pytest.approx(148.5)
    assert tr["exit_commission"] == pytest.approx(163.35)
    assert tr["exit_tax"] == pytest.approx(tax)
    assert tr["pnl"] == pytest.approx(pnl)
    assert res.equity.iloc[-1] == pytest.approx(final)
    assert res.metrics["total_return"] == pytest.approx(final / 1_000_000 - 1)


def test_weight_split_and_leftover_cash():
    """비중 0.5 씩 두 종목: 각 배정 자금 안에서만 매수, 남은 비중은 현금."""
    df = make_ohlcv([10_500, 9_900, 10_200, 10_300], opens=[10_500, 10_400, 10_000, 10_100])
    spec = base_spec_dict(
        universe=[{"ticker": "A", "name": "A", "weight": 0.5}, {"ticker": "B", "name": "B", "weight": 0.3}],
        entry={"logic": "all", "conditions": [{"left": "CLOSE", "op": "cross_below", "right": 10_000}]},
        exit={"logic": "any", "conditions": [], "stop_loss": None, "take_profit": None, "max_hold_days": 5},
        backtest={"start": "2000-01-01", "end": "2100-12-31", "initial_cash": 1_000_000},
    )
    res = run_backtest(DemoSpec.model_validate(spec), data={"A": df, "B": df.copy()})
    shares = {t["ticker"]: t["shares"] for t in res.trades}
    assert shares == {"A": 49, "B": 29}   # floor(500,000/10,001.5)=49, floor(300,000/10,001.5)=29
