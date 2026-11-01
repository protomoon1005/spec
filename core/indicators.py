"""B3 지표 계산. 모든 지표는 T일 종가(및 그 이전)까지의 데이터만 사용한다.

- 롤링/재귀 계산만 사용하고 shift(-k)·center=True·전체기간 통계는 쓰지 않는다.
- HIGH_N_20 / LOW_N_20 은 당일을 제외한 직전 20일 (shift(1) 후 rolling).
- 워밍업 구간은 NaN. NaN 이 낀 조건은 거짓으로 처리된다 (backtest.compute_signals).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

INDICATOR_IDS: list[str] = [
    "CLOSE", "OPEN", "HIGH", "LOW", "VOLUME",
    "SMA_5", "SMA_10", "SMA_20", "SMA_60", "SMA_120",
    "EMA_12", "EMA_26",
    "RSI_14",
    "BB_UPPER_20", "BB_LOWER_20",
    "VOL_SMA_20",
    "HIGH_N_20", "LOW_N_20",
]


def rsi_wilder(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder RSI. 첫 평균은 n개 변화의 단순평균, 이후 avg = (prev*(n-1) + x)/n.

    처음 n개 값(인덱스 0..n-1)은 NaN. 평균 손실이 0이면 100.
    """
    close = close.astype(float)
    values = close.to_numpy()
    out = np.full(len(values), np.nan)
    if len(values) <= n:
        return pd.Series(out, index=close.index)
    diff = np.diff(values)
    gains = np.where(diff > 0, diff, 0.0)
    losses = np.where(diff < 0, -diff, 0.0)
    avg_g = gains[:n].mean()
    avg_l = losses[:n].mean()

    def _rsi(g: float, l: float) -> float:
        if l == 0:
            return 100.0 if g > 0 else 50.0
        return 100.0 - 100.0 / (1.0 + g / l)

    out[n] = _rsi(avg_g, avg_l)
    for i in range(n + 1, len(values)):
        avg_g = (avg_g * (n - 1) + gains[i - 1]) / n
        avg_l = (avg_l * (n - 1) + losses[i - 1]) / n
        out[i] = _rsi(avg_g, avg_l)
    return pd.Series(out, index=close.index)


def ema(close: pd.Series, n: int) -> pd.Series:
    """EMA, α=2/(n+1), 첫 값부터 재귀(adjust=False). n개 미만 구간은 NaN.

    주의: TA-Lib 은 첫 n일 SMA 로 시드한다. 초기 구간 값이 조금 다를 수 있다 (수렴 후 동일).
    """
    return close.astype(float).ewm(span=n, adjust=False, min_periods=n).mean()


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """OHLCV(Open/High/Low/Close/Volume) → 지표 id 를 열로 갖는 DataFrame."""
    c = df["Close"].astype(float)
    out = pd.DataFrame(index=df.index)
    out["CLOSE"] = c
    out["OPEN"] = df["Open"].astype(float)
    out["HIGH"] = df["High"].astype(float)
    out["LOW"] = df["Low"].astype(float)
    out["VOLUME"] = df["Volume"].astype(float)
    for n in (5, 10, 20, 60, 120):
        out[f"SMA_{n}"] = c.rolling(n, min_periods=n).mean()
    for n in (12, 26):
        out[f"EMA_{n}"] = ema(c, n)
    out["RSI_14"] = rsi_wilder(c, 14)
    mid = c.rolling(20, min_periods=20).mean()
    std = c.rolling(20, min_periods=20).std(ddof=0)  # 모표준편차 (TA-Lib 과 동일)
    out["BB_UPPER_20"] = mid + 2 * std
    out["BB_LOWER_20"] = mid - 2 * std
    out["VOL_SMA_20"] = out["VOLUME"].rolling(20, min_periods=20).mean()
    out["HIGH_N_20"] = out["HIGH"].shift(1).rolling(20, min_periods=20).max()
    out["LOW_N_20"] = out["LOW"].shift(1).rolling(20, min_periods=20).min()
    return out[INDICATOR_IDS]
