"""미래 참조: T+1 이후 데이터(종목·코스피200·VIX)를 바꿔도 T일까지의 지표·신호·체결이 그대로여야 한다."""
import numpy as np
import pandas as pd
import pytest

from conftest import STOCK, cmp, ind, make_ohlcv, strategy_dict, wavy_ohlcv
from core.backtest import condition_series, run_backtest
from core.dictionary import INDICATORS
from core.indicators import align_us_to_kr, indicator_series
from core.lookahead import perturb_after as _perturb_after   # t 이후(t+1~) 행을 결정론적으로 크게 바꾼다 (baseline 도 같은 함수)
from core.schema import Strategy


def _data(n=300):
    asset = wavy_ohlcv(n)
    ks200 = wavy_ohlcv(n, phase=1.3, base=300.0)
    vix = make_ohlcv([18 + 8 * np.sin(i / 11.0) for i in range(n)], volume=[0] * n)  # 미국 날짜로 취급
    return {"TEST01": asset, "KS200": ks200, "VIX": vix}


def _perturb_all(data, t):
    return {k: _perturb_after(v, t) for k, v in data.items()}


CUTS = [30, 75, 140, 220]
TRADE_CUTS = [130, 180, 240, 280]   # 워밍업(120일) 이후라 거래가 존재하는 시점

RICH = strategy_dict(rules={
    "target": "each",
    "entry": {"any": [
        cmp(ind("rsi", 14), "cross_below", 35),
        {"all": [cmp(ind("close"), "cross_above", ind("ma", 20)),
                 cmp(ind("close", of="KOSPI200"), ">", ind("ma", 60, of="KOSPI200"))]},
        cmp(ind("volume_ratio", 20), ">=", 2),
        {"all": [cmp(ind("ret", 5), "<", -3), cmp(ind("vix", of="VIX"), "<", 22)]},
        cmp(ind("ma", 5), "cross_above", ind("ma", 120)),
    ]},
    "exit": {"any": [
        cmp(ind("close"), "cross_below", ind("ma", 10)),
        cmp(ind("vol", 20), ">", 3),
        cmp(ind("ret", 60, of="KOSPI200"), "<=", -5),
        cmp(ind("entry_return"), "<=", -5),
        cmp(ind("entry_return"), ">=", 10),
        cmp(ind("holding_days"), ">=", 15),
    ]},
})


WINDOWED = [(name, w) for name, meta in INDICATORS.items() if not meta.get("position")
            and name != "vix" for w in meta["windows"]]


@pytest.mark.parametrize("t", CUTS)
@pytest.mark.parametrize("name,window", WINDOWED)
def test_indicators_unchanged_up_to_t(t, name, window):
    df = wavy_ohlcv()
    a = indicator_series(df, name, window)
    b = indicator_series(_perturb_after(df, t), name, window)
    pd.testing.assert_series_equal(a.iloc[: t + 1], b.iloc[: t + 1])
    assert not a.iloc[t + 2:].equals(b.iloc[t + 2:]), "섭동이 반영되지 않으면 테스트가 공허하다"


@pytest.mark.parametrize("t", CUTS)
def test_vix_alignment_unchanged_up_to_t(t):
    vix = _data()["VIX"]
    kr = vix.index
    a = align_us_to_kr(vix["Close"], kr)
    b = align_us_to_kr(_perturb_after(vix, t)["Close"], kr)
    # 미국 t일 값은 국내 t+1일부터 쓰인다 → 국내 t+1일까지 같아야 한다
    pd.testing.assert_series_equal(a.iloc[: t + 2], b.iloc[: t + 2])
    assert a.iloc[t + 2] != b.iloc[t + 2]


@pytest.mark.parametrize("t", CUTS)
def test_signals_unchanged_up_to_t(t):
    spec = Strategy.model_validate(RICH)
    data = _data()
    cal = data["TEST01"].index
    a = condition_series(spec.rules.entry, "TEST01", data, cal)
    b = condition_series(spec.rules.entry, "TEST01", _perturb_all(data, t), cal)
    pd.testing.assert_series_equal(a.iloc[: t + 1], b.iloc[: t + 1])


@pytest.mark.parametrize("t", TRADE_CUTS)
def test_fills_and_equity_unchanged_up_to_t(t):
    spec = Strategy.model_validate(RICH)
    data = _data()
    ra = run_backtest(spec, data=data, instruments={"TEST01": STOCK})
    rb = run_backtest(spec, data=_perturb_all(data, t), instruments={"TEST01": STOCK})
    cut = data["TEST01"].index[t].strftime("%Y-%m-%d")
    fa = [f for f in ra.fills if f["date"] <= cut]
    fb = [f for f in rb.fills if f["date"] <= cut]
    assert fa == fb
    assert len(fa) > 0, "검증할 체결이 없으면 테스트가 공허하다"
    pd.testing.assert_series_equal(ra.equity.loc[:cut], rb.equity.loc[:cut])


def test_harness_detects_a_leaky_indicator():
    """음성 대조군: 일부러 미래를 보는 지표(shift(-1))는 같은 섭동 검사에서 잡혀야 한다."""
    df = wavy_ohlcv()
    t = 140
    leaky = lambda d: d["Close"].shift(-1).rolling(5).mean()  # noqa: E731 — 미래 참조
    a, b = leaky(df), leaky(_perturb_after(df, t))
    assert not a.iloc[: t + 1].equals(b.iloc[: t + 1])


def test_harness_detects_same_day_vix():
    """음성 대조군: VIX 를 같은 날짜로 붙이면(미국 t일 → 국내 t일) 섭동 검사에서 잡혀야 한다."""
    vix = _data()["VIX"]
    t = 140
    same_day = lambda d: d["Close"].reindex(vix.index)  # noqa: E731 — 미국 t일 종가를 국내 t일에 사용
    a, b = same_day(vix), same_day(_perturb_after(vix, t))
    assert not a.iloc[: t + 2].equals(b.iloc[: t + 2])
