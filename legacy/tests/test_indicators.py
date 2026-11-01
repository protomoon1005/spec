"""5. RSI_14 Wilder 방식 + 기타 지표 정의 확인."""
import numpy as np
import pandas as pd
import pytest

from legacy.tests.conftest import make_ohlcv
from legacy.indicators import compute_indicators, rsi_wilder

# 출처: StockCharts ChartSchool "Relative Strength Index (RSI)" 계산 예시 스프레드시트 (cs-rsi.xls)
# Wilder 평활: 첫 평균 = 14개 변화의 단순평균, 이후 (prev*13 + 현재)/14. RSI 는 소수 둘째 자리 표시값.
# 주의: 가격·RSI 값 모두 원 출처를 다시 열어 확인하지 않고 옮겨 적은 것이다 → 팀이 원본과 한 번 대조할 것.
# 가격은 반드시 소수 넷째 자리 원값을 써야 한다. 둘째 자리로 반올림한 가격으로는 첫 RSI 가 70.46 이 되어
# 표시값 70.53 과 어긋난다 (이 차이로 처음 테스트가 실패했었다).
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
    got = rsi.iloc[14:].round(2).tolist()
    assert got == pytest.approx(WILDER_RSI_FROM_IDX14, abs=0.011)


def test_rsi_hand_computed_small():
    # n=2 로 손계산: 변화 +1, -1, +2
    # 첫 평균(idx2): gain (1+0)/2=0.5, loss (0+1)/2=0.5 → RSI 50
    # idx3: gain (0.5*1+2)/2=1.25, loss (0.5*1+0)/2=0.25 → RS 5 → RSI 100-100/6 = 83.333...
    rsi = rsi_wilder(pd.Series([10.0, 11.0, 10.0, 12.0]), 2)
    assert np.isnan(rsi.iloc[0]) and np.isnan(rsi.iloc[1])
    assert rsi.iloc[2] == pytest.approx(50.0)
    assert rsi.iloc[3] == pytest.approx(250 / 3)


def test_rsi_all_gains_is_100():
    rsi = rsi_wilder(pd.Series(np.arange(1.0, 30.0)), 14)
    assert (rsi.iloc[14:] == 100).all()


def test_sma_bb_vol():
    closes = list(range(1, 31))
    df = make_ohlcv(closes, volume=[10 * (i + 1) for i in range(30)])
    ind = compute_indicators(df)
    assert ind["SMA_5"].iloc[4] == pytest.approx(3.0)
    assert pd.isna(ind["SMA_5"].iloc[3])
    window = np.arange(11, 31, dtype=float)
    std = window.std(ddof=0)
    assert ind["BB_UPPER_20"].iloc[-1] == pytest.approx(window.mean() + 2 * std)
    assert ind["BB_LOWER_20"].iloc[-1] == pytest.approx(window.mean() - 2 * std)
    assert ind["VOL_SMA_20"].iloc[-1] == pytest.approx(np.mean([10 * k for k in range(11, 31)]))
    assert ind["CLOSE"].iloc[-1] == 30
