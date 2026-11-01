"""1. 미래 참조: T+1 이후 가격을 바꿔도 T일까지의 지표·신호·체결이 그대로여야 한다."""
import numpy as np
import pandas as pd
import pytest

from conftest import base_spec_dict, wavy_ohlcv
from core.backtest import compute_signals, run_backtest
from core.indicators import INDICATOR_IDS, compute_indicators
from core.schema import DemoSpec


def _perturb_after(df: pd.DataFrame, t: int) -> pd.DataFrame:
    """t 이후(t+1~) 행을 결정론적으로 크게 바꾼다."""
    out = df.copy()
    tail = out.index[t + 1 :]
    k = np.arange(len(tail))
    for col in ["Open", "High", "Low", "Close"]:
        out.loc[tail, col] = out.loc[tail, col] * (2.5 + 0.7 * np.sin(k))
    out.loc[tail, "Low"] = out.loc[tail, ["Open", "Close", "Low"]].min(axis=1) * 0.5
    out.loc[tail, "High"] = out.loc[tail, ["Open", "Close", "High"]].max(axis=1) * 1.5
    out.loc[tail, "Volume"] = out.loc[tail, "Volume"] * 37
    return out


CUTS = [30, 75, 140, 220]
TRADE_CUTS = [70, 120, 180, 240]   # 워밍업 이후라 거래가 존재하는 시점

RICH_SPEC = base_spec_dict(
    entry={
        "logic": "any",
        "conditions": [
            {"left": "RSI_14", "op": "cross_below", "right": 30},
            {"left": "CLOSE", "op": "cross_above", "right": "HIGH_N_20"},
            {"left": "VOLUME", "op": "gt", "right": {"ref": "VOL_SMA_20", "mul": 2}},
            {"left": "EMA_12", "op": "cross_above", "right": "EMA_26"},
        ],
    },
    exit={
        "logic": "any",
        "conditions": [
            {"left": "CLOSE", "op": "cross_below", "right": "SMA_5"},
            {"left": "CLOSE", "op": "lt", "right": "LOW_N_20"},
            {"left": "CLOSE", "op": "gt", "right": "BB_UPPER_20"},
        ],
        "stop_loss": 0.05,
        "take_profit": 0.1,
        "max_hold_days": 15,
    },
)


@pytest.mark.parametrize("t", CUTS)
def test_indicators_unchanged_up_to_t(t):
    df = wavy_ohlcv()
    a = compute_indicators(df)
    b = compute_indicators(_perturb_after(df, t))
    assert set(INDICATOR_IDS) <= set(a.columns)
    pd.testing.assert_frame_equal(a.iloc[: t + 1], b.iloc[: t + 1])
    # 변경이 실제로 반영됐는지 (테스트가 공허하지 않음을 확인)
    assert not a["SMA_5"].iloc[t + 5 :].equals(b["SMA_5"].iloc[t + 5 :])


@pytest.mark.parametrize("t", CUTS)
def test_signals_unchanged_up_to_t(t):
    spec = DemoSpec.model_validate(RICH_SPEC)
    df = wavy_ohlcv()
    for rule in (spec.entry, spec.exit):
        a = compute_signals(compute_indicators(df), rule)
        b = compute_signals(compute_indicators(_perturb_after(df, t)), rule)
        pd.testing.assert_series_equal(a.iloc[: t + 1], b.iloc[: t + 1])


@pytest.mark.parametrize("t", TRADE_CUTS)
def test_trades_and_equity_unchanged_up_to_t(t):
    spec = DemoSpec.model_validate(RICH_SPEC)
    df = wavy_ohlcv()
    ra = run_backtest(spec, data={"TEST01": df})
    rb = run_backtest(spec, data={"TEST01": _perturb_after(df, t)})
    cut = df.index[t]
    # 체결일이 T 이하인 거래 행위(매수/매도)는 동일해야 한다
    def events(res):
        ev = []
        for tr in res.trades:
            if tr["entry_date"] <= cut.strftime("%Y-%m-%d"):
                ev.append(("B", tr["entry_date"], tr["entry_price"], tr["shares"]))
            if tr["exit_date"] and tr["exit_date"] <= cut.strftime("%Y-%m-%d"):
                ev.append(("S", tr["exit_date"], tr["exit_price"], tr["exit_reason"]))
        return ev
    assert events(ra) == events(rb)
    assert len(events(ra)) > 0, "검증할 거래가 없으면 테스트가 공허하다"
    pd.testing.assert_series_equal(ra.equity.loc[:cut], rb.equity.loc[:cut])


def test_high_low_n_20_exclude_today():
    df = wavy_ohlcv(60)
    ind = compute_indicators(df)
    i = 40
    assert ind["HIGH_N_20"].iloc[i] == df["High"].iloc[i - 20 : i].max()
    assert ind["LOW_N_20"].iloc[i] == df["Low"].iloc[i - 20 : i].min()
    # 당일 고가를 아주 크게 바꿔도 당일 HIGH_N_20 은 불변
    df2 = df.copy()
    df2.iloc[i, df2.columns.get_loc("High")] = 1e9
    assert compute_indicators(df2)["HIGH_N_20"].iloc[i] == ind["HIGH_N_20"].iloc[i]
    assert pd.isna(ind["HIGH_N_20"].iloc[19]) and not pd.isna(ind["HIGH_N_20"].iloc[20])


def test_harness_detects_a_leaky_indicator():
    """음성 대조군: 일부러 미래를 보는 지표(shift(-1))는 같은 섭동 검사에서 잡혀야 한다."""
    df = wavy_ohlcv()
    t = 140
    leaky = lambda d: d["Close"].shift(-1).rolling(5).mean()  # noqa: E731 — 미래 참조
    a, b = leaky(df), leaky(_perturb_after(df, t))
    assert not a.iloc[: t + 1].equals(b.iloc[: t + 1])
