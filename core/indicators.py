"""v0.2 지표 계산. 모든 지표는 t일 종가(및 그 이전)까지의 데이터만 사용한다.

- 롤링/재귀 계산만 쓰고 shift(-k)·center=True·전체 기간 통계는 쓰지 않는다.
- 워밍업 구간은 NaN. NaN 이 낀 비교는 거짓이다 (core.backtest).
- 다른 대상(of)의 값은 그 대상의 이력으로 계산한 뒤 판단 달력에 '과거 방향으로만' 맞춘다.
- VIX 는 미국 날짜 기준이라 국내 t일에는 t보다 앞선 마지막 미국 거래일(= 미국 t-1일) 종가를 쓴다.

단위
- ret: % (10 = 10%). (오늘 종가 - n일 전 종가) / n일 전 종가 × 100
- vol: 일간 수익률(%)의 20일 모표준편차. 연율화하지 않는다 (v0.2 에 정의가 없어 데모에서 정함)
- volume_ratio: 오늘 거래량 / 직전 20일(오늘 제외) 평균 거래량
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from core.dictionary import INDICATORS

COMPUTABLE = ("close", "ma", "rsi", "ret", "vol", "volume_ratio")


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


def indicator_series(df: pd.DataFrame, name: str, window: Optional[int]) -> pd.Series:
    """OHLCV → 지표 하나. 사전에 없는 지표·기간이면 ValueError."""
    if name not in COMPUTABLE:
        raise ValueError(f"가격 데이터로 계산하는 지표가 아닙니다: {name}")
    if window not in INDICATORS[name]["windows"]:
        raise ValueError(f"{name} 의 기간은 {INDICATORS[name]['windows']} 중 하나여야 합니다: {window}")
    c = df["Close"].astype(float)
    if name == "close":
        out = c
    elif name == "ma":
        out = c.rolling(window, min_periods=window).mean()
    elif name == "rsi":
        out = rsi_wilder(c, window)
    elif name == "ret":
        prev = c.shift(window)
        out = (c - prev) / prev * 100.0
    elif name == "vol":
        r = (c - c.shift(1)) / c.shift(1) * 100.0
        out = r.rolling(window, min_periods=window).std(ddof=0)
    else:  # volume_ratio
        v = df["Volume"].astype(float)
        base = v.shift(1).rolling(window, min_periods=window).mean()
        out = (v / base).replace([np.inf, -np.inf], np.nan)
    return out.rename(f"{name}_{window}" if window else name)


def align_to_calendar(s: pd.Series, calendar: pd.DatetimeIndex) -> pd.Series:
    """다른 대상의 값을 판단 달력에 맞춘다. 그날 값이 없으면 직전 값(과거 방향 ffill)만 쓴다."""
    full = s.reindex(s.index.union(calendar)).ffill()
    return full.reindex(calendar)


def align_us_to_kr(us: pd.Series, kr_calendar: pd.DatetimeIndex) -> pd.Series:
    """미국 날짜 시계열 → 국내 달력. 국내 d일 값 = 날짜가 d보다 앞선 마지막 미국 거래일의 값.
    같은 날짜의 미국 값은 국내 장 마감 시점에 아직 없으므로 절대 쓰지 않는다."""
    us = us.sort_index()
    pos = us.index.searchsorted(kr_calendar, side="left") - 1
    vals = np.where(pos >= 0, us.to_numpy()[np.clip(pos, 0, None)], np.nan) if len(us) else \
        np.full(len(kr_calendar), np.nan)
    return pd.Series(vals.astype(float), index=kr_calendar, name=us.name)
