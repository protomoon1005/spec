# 조건형 전략서(v0.2)의 지표 시계열. DB 를 모르는 순수 함수이고 stdlib 만 쓴다.
#
# 설명을 docstring 이 아니라 주석에 두는 이유는 features.py 와 같다(as_of 가드 사정권).
#
# ── 무엇을 보장하나 ──────────────────────────────────────────────────
# series[k] 는 bars[0..k] 만으로 계산한다. 앞으로 나올 행은 보지 않는다.
# 테스트가 이것을 두 가지로 확인한다(tests/test_backtest_signals.py).
#   1) 시계열 전체를 한 번에 계산한 값 = bars[:k+1] 로 잘라서 계산한 값
#   2) 같은 정의인 features.py 의 지표(_wilder_rsi, _return_over, _return_stdev)와 값이 같다
# 백테스트는 이 시계열을 미리 한 번 계산해 두고 날짜마다 꺼내 쓴다(설계 문서 "지표 표를 한 번 계산").
#
# ── 지표 정의 (v0.2, 2026-11-02) ─────────────────────────────────────
# C = 종가, V = 거래량, k = 그날의 행 번호. 이력이 모자라면 None(비교는 거짓이 된다).
#   close              C_k
#   ma N               최근 N개 종가의 단순평균                       필요 N행
#   rsi 14             Wilder RSI. features.py 와 같은 정의            필요 15행
#   ret N              C_k / C_{k-N} - 1. 소수(0.10 = 10%)             필요 N+1행
#   vol 20             최근 20개 일간 수익률의 표본표준편차(n-1). 소수   필요 21행
#   volume_ratio 20    V_k / (직전 20일 거래량 평균). 오늘은 평균에서 뺀다 필요 21행
# volume_ratio 는 "거래량이 20일 평균의 3배" 를 오늘과 지난 20일의 비교로 읽는다. features.py 의
# volume_ratio_20 은 오늘을 평균에 넣어서 정의가 다르다. 그래서 여기서 따로 계산한다.
# 거래정지일 행은 지우지 않는다. 그날 종가(전날 값이 그대로 들어 있다)로 그대로 계산한다(설계 문서).
from __future__ import annotations

import math
from collections.abc import Sequence

from app.backtest.features import _return_stdev

# 가격으로 계산하는 지표. entry_return · holding_days 는 보유 상태로 계산하고(rules_engine),
# vix 는 데모 범위 밖이다
PRICE_INDICATORS = ("close", "ma", "rsi", "ret", "vol", "volume_ratio")


def indicator_series(
    closes: Sequence[float], volumes: Sequence[float], indicator: str, window: int | None
) -> list[float | None]:
    if indicator not in PRICE_INDICATORS:
        raise ValueError(f"가격으로 계산하는 지표가 아니다: {indicator}")
    if indicator == "close":
        return [float(c) for c in closes]
    if window is None or window <= 0:
        raise ValueError(f"{indicator} 에는 기간이 있어야 한다")
    if indicator == "ma":
        return _moving_average(closes, window)
    if indicator == "rsi":
        return _rsi(closes, window)
    if indicator == "ret":
        return [_ret(closes, k, window) for k in range(len(closes))]
    if indicator == "vol":
        return [_return_stdev(list(closes[: k + 1]), window) for k in range(len(closes))]
    return _volume_ratio(volumes, window)


def _moving_average(closes: Sequence[float], window: int) -> list[float | None]:
    out: list[float | None] = []
    for k in range(len(closes)):
        if k + 1 < window:
            out.append(None)
        else:
            out.append(math.fsum(closes[k + 1 - window : k + 1]) / window)
    return out


def _rsi(closes: Sequence[float], period: int) -> list[float | None]:
    # Wilder 평활은 앞에서부터 이어 가는 계산이라, 한 번에 이어 계산해도 잘라서 계산한 값과 같다.
    # features._wilder_rsi 를 행마다 부르면 O(n^2) 이라 같은 식을 이어 계산한다
    n = len(closes)
    out: list[float | None] = [None] * n
    if n < period + 1:
        return out
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, n)]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, n)]
    average_gain = math.fsum(gains[:period]) / period
    average_loss = math.fsum(losses[:period]) / period
    out[period] = _rsi_value(average_gain, average_loss)
    for index in range(period, len(gains)):
        average_gain = (average_gain * (period - 1) + gains[index]) / period
        average_loss = (average_loss * (period - 1) + losses[index]) / period
        out[index + 1] = _rsi_value(average_gain, average_loss)
    return out


def _rsi_value(average_gain: float, average_loss: float) -> float:
    if average_loss == 0.0:
        return 100.0 if average_gain > 0.0 else 50.0
    return 100.0 - 100.0 / (1.0 + average_gain / average_loss)


def _ret(closes: Sequence[float], k: int, window: int) -> float | None:
    if k < window:
        return None
    base = closes[k - window]
    if base <= 0:
        return None
    return closes[k] / base - 1.0


def _volume_ratio(volumes: Sequence[float], window: int) -> list[float | None]:
    out: list[float | None] = []
    for k in range(len(volumes)):
        if k < window:
            out.append(None)
            continue
        average = math.fsum(volumes[k - window : k]) / window
        out.append(volumes[k] / average if average > 0 else None)
    return out
