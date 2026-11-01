"""v0.2 지표 정의: close, ma, rsi(Wilder), ret, vol, volume_ratio, vix(미국 t-1일)."""
import numpy as np
import pandas as pd
import pytest

from conftest import make_ohlcv
from core.indicators import align_to_calendar, align_us_to_kr, indicator_series, rsi_wilder

# 출처: StockCharts ChartSchool "Relative Strength Index (RSI)" 계산 예시 스프레드시트 (cs-rsi.xls)
# Wilder 평활: 첫 평균 = 14개 변화의 단순평균, 이후 (prev*13 + 현재)/14. RSI 는 소수 둘째 자리 표시값.
# 주의: 가격·RSI 값 모두 원 출처를 다시 열어 확인하지 않고 옮겨 적은 것이다 → 팀이 원본과 한 번 대조할 것.
WILDER_PRICES = [
    44.3389, 44.0902, 44.1497, 43.6124, 44.3278, 44.8264, 45.0955, 45.4245, 45.8433, 46.0826,
    45.8931, 46.0328, 45.6140, 46.2820, 46.2820, 46.0028, 46.0328, 46.4116, 46.2222, 45.6439,
    46.2122, 46.2521, 45.7137, 46.4515, 45.7835, 45.3548, 44.0288, 44.1783, 44.2181, 44.5672,
    43.4205, 42.6628, 43.1314,
]
WILDER_RSI_FROM_IDX14 = [
    70.53, 66.32, 66.55, 69.41, 66.36, 57.97, 62.93, 63.26, 56.06, 62.38,
    54.71, 50.42, 39.99, 41.46, 41.87, 45.46, 37.30, 33.08, 37.77,
]


def test_rsi_wilder_known_values():
    rsi = rsi_wilder(pd.Series(WILDER_PRICES), 14)
    assert rsi.iloc[:14].isna().all()
    assert rsi.iloc[14:].round(2).tolist() == pytest.approx(WILDER_RSI_FROM_IDX14, abs=0.011)


def test_rsi_hand_computed_small():
    # n=2 손계산: 변화 +1, -1, +2 → idx2 RSI 50, idx3 RS 5 → 83.333...
    rsi = rsi_wilder(pd.Series([10.0, 11.0, 10.0, 12.0]), 2)
    assert np.isnan(rsi.iloc[0]) and np.isnan(rsi.iloc[1])
    assert rsi.iloc[2] == pytest.approx(50.0)
    assert rsi.iloc[3] == pytest.approx(250 / 3)


def test_rsi_all_gains_is_100():
    rsi = rsi_wilder(pd.Series(np.arange(1.0, 30.0)), 14)
    assert (rsi.iloc[14:] == 100).all()


def test_close_ma_ret():
    df = make_ohlcv(list(range(1, 31)))
    assert indicator_series(df, "close", None).iloc[-1] == 30
    ma5 = indicator_series(df, "ma", 5)
    assert pd.isna(ma5.iloc[3]) and ma5.iloc[4] == pytest.approx(3.0)
    ret5 = indicator_series(df, "ret", 5)          # % 단위: (30/25 - 1) × 100 = 20
    assert pd.isna(ret5.iloc[4]) and ret5.iloc[-1] == pytest.approx(20.0)
    assert indicator_series(df, "ret", 1).iloc[1] == pytest.approx(100.0)   # 1 → 2


def test_vol_is_std_of_daily_returns_pct():
    closes = [100.0 + (1 if i % 2 else -1) * (i % 5) for i in range(40)]
    df = make_ohlcv(closes)
    r = pd.Series(closes).pct_change() * 100
    expected = r.iloc[-20:].std(ddof=0)
    got = indicator_series(df, "vol", 20)
    assert got.iloc[-1] == pytest.approx(expected)
    assert pd.isna(got.iloc[19]) and not pd.isna(got.iloc[20])   # 수익률 20개 = 가격 21개 필요


def test_volume_ratio_excludes_today():
    vol = [100.0] * 20 + [300.0, 50.0]
    df = make_ohlcv([10.0] * 22, volume=vol)
    vr = indicator_series(df, "volume_ratio", 20)
    assert pd.isna(vr.iloc[19])
    assert vr.iloc[20] == pytest.approx(3.0)                 # 300 / 직전 20일 평균 100
    assert vr.iloc[21] == pytest.approx(50.0 / ((100 * 19 + 300) / 20))


def test_unknown_indicator_or_window_raises():
    df = make_ohlcv(list(range(1, 31)))
    with pytest.raises(ValueError):
        indicator_series(df, "ema", 12)
    with pytest.raises(ValueError):
        indicator_series(df, "ma", 7)


def test_align_to_calendar_only_fills_forward():
    s = pd.Series([1.0, 2.0, 3.0], index=pd.to_datetime(["2024-01-02", "2024-01-04", "2024-01-08"]))
    cal = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"])
    out = align_to_calendar(s, cal)
    assert out.tolist() == [1.0, 1.0, 2.0, 2.0, 3.0]


def test_vix_uses_previous_us_day_close():
    """국내 t일 판단에는 미국 t-1일(= t보다 앞선 마지막 미국 거래일) 종가."""
    us = pd.Series([20.0, 21.0, 22.0, 23.0],
                   index=pd.to_datetime(["2024-01-04", "2024-01-05", "2024-01-08", "2024-01-09"]))
    kr = pd.to_datetime(["2024-01-05", "2024-01-08", "2024-01-09", "2024-01-10"])
    out = align_us_to_kr(us, kr)
    # 1/5(금) → 미국 1/4, 1/8(월) → 미국 1/5(금), 1/9 → 1/8, 1/10 → 1/9
    assert out.tolist() == [20.0, 21.0, 22.0, 23.0]
    # 같은 날짜의 미국 값은 절대 쓰지 않는다
    us2 = us.copy()
    us2.loc["2024-01-09"] = 999.0
    assert align_us_to_kr(us2, kr).loc["2024-01-09"] == 22.0
