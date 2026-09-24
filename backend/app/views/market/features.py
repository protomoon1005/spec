# 시장분석 관점(T4)이 쓸 가격 기반 피처 계산. DB를 모르는 순수 함수다.
#
# 설명을 docstring이 아니라 주석에 두는 이유: 이 파일은 as_of 가드 사정권이다.
# scripts/check_asof_guard.py 가 문자열 리터럴(docstring 포함)에서 가드 대상
# 테이블명을 찾으면 CI가 실패한다. 주석은 AST 에 안 잡힌다.
# 계산 결과 dict 를 저장하는 것은 app/repositories/ 의 적재 함수 몫이고,
# 가격 조회 함수는 T3 에서 리포지토리에 붙인다 (지금 만들지 않는다).
#
# ── 왜 pandas-ta 가 아니라 stdlib 인가 ───────────────────────────────
# 이 모듈의 가장 중요한 산출물은 지표값이 아니라 "미래를 보지 않는다"는 보장이다.
# 그런데 pandas 는 base dependencies 에 없다 (ml extra 로만 들어온다). pandas 로
# 계산하면 미래 참조 금지 테스트에 requires_ml 이 붙어 CI 에서 빠지고, 이 프로젝트
# 에서 가장 중요한 성질이 매 PR 에서 검증되지 않는다.
# 그래서 계산 코어를 stdlib 만으로 짰다. DataFrame 은 _as_bars 가 받아 주므로
# 호출부는 그대로 pandas 를 쓸 수 있다 — pandas 를 import 하지 않고 덕 타이핑으로
# 다룬다. 나중에 pandas-ta 정의와의 일치가 필요해지면 그때 갈아끼우고
# feature_set_version 을 올린다 (정의가 바뀌므로 반드시 올려야 한다).
#
# ── feature_set_version ──────────────────────────────────────────────
# 형식: v<major>.<minor>-<피처셋 슬러그>.  예) v0.1-ta9
# 규칙 둘.
#   1) 피처 목록이나 계산식이 바뀌면 버전을 올린다. 같은 버전에 다른 정의가
#      섞이면 과거 결정을 재현할 수 없다.
#   2) 문자열만 보고 무엇이 들었는지 짐작할 수 있게 한다 (ta9 = 가격 기반 지표 9개).
# 버전별 피처 목록은 아래 FEATURE_SETS 상수에 남긴다 — 테이블을 새로 만드는 것은
# 스키마 변경이라 하지 않는다.
#
# ── 결측 처리 ────────────────────────────────────────────────────────
# 윈도우보다 이력이 짧으면 None 을 낸다. 0 으로 채우지 않는다 — "지표가 0이다"와
# "아직 계산할 이력이 모자란다"가 구분되지 않으면 모델이 그 차이를 학습해 버린다.
# 결측을 어떻게 다룰지는 스코어러의 몫이고, 데이터가 모자라면 중립을 낸다.
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta

CURRENT_FEATURE_SET_VERSION = "v0.1-ta9"

# ── v0.1-ta9 지표 정의 (정본) ────────────────────────────────────────
# C_t = t 시점 종가, H/L = 고가/저가, V = 거래량. t 는 as_of 이하의 마지막 행.
# "필요 행수"보다 이력이 짧으면 None(결측)이다. 0 으로 채우지 않는다.
#
#   ret_1            C_t / C_{t-1}  − 1                                필요 2행
#   ret_5            C_t / C_{t-5}  − 1                                필요 6행
#   ret_20           C_t / C_{t-20} − 1                                필요 21행
#   ma_gap_20        C_t / SMA20(C) − 1      SMA = 단순평균             필요 20행
#   ma_gap_60        C_t / SMA60(C) − 1                                필요 60행
#   rsi_14           Wilder RSI(14)                                    필요 15행
#   atr_14_pct       Wilder ATR(14) / C_t                              필요 15행
#   vol_20           최근 20개 일간 단순수익률의 표본표준편차(n−1)        필요 21행
#   volume_ratio_20  V_t / SMA20(V)                                    필요 20행
#
# Wilder RSI(14) 상세 — 구현체마다 가장 많이 갈리는 지표다:
#   delta_i = C_i − C_{i-1}, gain = max(delta, 0), loss = max(−delta, 0)
#   seed:   avgGain = mean(gain[0:14]),  avgLoss = mean(loss[0:14])    ← 단순평균
#   이후:   avgGain = (avgGain·13 + gain_i) / 14                        ← Wilder smoothing
#   RS = avgGain / avgLoss,  RSI = 100 − 100/(1+RS)
#   avgLoss == 0 이면: avgGain > 0 → 100.0, 둘 다 0(완전 평탄) → 50.0
#   (gain/loss 단순이동평균을 쓰는 구현과는 값이 다르다. 우리는 Wilder 다.)
#
# Wilder ATR(14) 상세:
#   TR_i = max(H_i − L_i, |H_i − C_{i-1}|, |L_i − C_{i-1}|)
#   seed = mean(TR[0:14]), 이후 ATR = (ATR·13 + TR_i) / 14              ← Wilder smoothing
#   (SMA 기반 ATR 구현과는 값이 다르다.)
#
# ── 재현성 주의: Wilder 계열은 기억이 무한하다 ───────────────────────
# rsi_14 와 atr_14_pct 는 seed 이후 매 행을 지수적으로 섞으므로, 같은 as_of 라도
# **입력 이력을 어디서부터 줬는지에 따라 값이 달라진다.** 200행 랜덤워크로 실측한
# 차이(2026-09-12):
#     이력 201행 -> rsi 61.7797 / 이력 141행 -> 61.7807 / 이력 31행 -> 65.0775
# 윈도우 기반 피처(ma_gap·vol·volume_ratio·ret)는 이력 길이와 무관하게 같다.
# ★ 그래서 워밍업 행수는 권고가 아니라 **피처셋 정의의 일부**다. 이 값이 달라지면
#   같은 v0.1-ta9 로 다른 값이 나오고, 재현성의 축인 버전 문자열이 거짓말을 한다.
#   적재 배치와 백테스트 러너는 **as_of 마다 직전 FEATURE_WARMUP_ROWS 행**을 입력으로
#   준다 — 이건 계약이다. 120행이면 전체 이력 값과 소수 둘째 자리까지 일치한다
#   (tests/test_market_features.py 가 고정한다).
FEATURE_WARMUP_ROWS = 120

# 버전 -> 피처 이름 목록. 데모 계획이 "소규모 구간과 적은 피처로 먼저 돌아가게
# 만들고 정확도는 12월에 올린다"고 못 박아서 아홉 개로 시작한다.
FEATURE_SETS: dict[str, tuple[str, ...]] = {
    "v0.1-ta9": (
        "ret_1",  # 1거래일 수익률
        "ret_5",  # 5거래일 수익률
        "ret_20",  # 20거래일 수익률
        "ma_gap_20",  # 종가 / 20일 단순이동평균 - 1 (이격도)
        "ma_gap_60",  # 종가 / 60일 단순이동평균 - 1
        "rsi_14",  # Wilder RSI(14)
        "atr_14_pct",  # Wilder ATR(14) / 종가
        "vol_20",  # 최근 20거래일 수익률 표본표준편차
        "volume_ratio_20",  # 거래량 / 20일 평균 거래량
    ),
}

_PRECISION = 6
_RSI_PERIOD = 14
_ATR_PERIOD = 14


@dataclass(frozen=True)
class PriceBar:
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float


def feature_names(version: str = CURRENT_FEATURE_SET_VERSION) -> tuple[str, ...]:
    if version not in FEATURE_SETS:
        raise ValueError(f"알 수 없는 피처셋 버전: {version!r} (아는 것: {sorted(FEATURE_SETS)})")
    return FEATURE_SETS[version]


def compute_features(
    prices, *, as_of: date, version: str = CURRENT_FEATURE_SET_VERSION
) -> dict[str, float | None]:
    """as_of 시점 기준 피처 dict. 반환 키는 항상 그 버전의 전체 목록이다.

    prices 는 pandas DataFrame 이거나, PriceBar / 매핑의 시퀀스다.
    as_of 이후 행은 계산 전에 잘라낸다 — 그게 이 함수의 핵심 보장이다.
    """
    names = feature_names(version)
    bars = [bar for bar in _as_bars(prices) if bar.trade_date <= as_of]

    closes = [bar.close for bar in bars]
    volumes = [bar.volume for bar in bars]

    computed: dict[str, float | None] = {
        "ret_1": _return_over(closes, 1),
        "ret_5": _return_over(closes, 5),
        "ret_20": _return_over(closes, 20),
        "ma_gap_20": _ma_gap(closes, 20),
        "ma_gap_60": _ma_gap(closes, 60),
        "rsi_14": _wilder_rsi(closes, _RSI_PERIOD),
        "atr_14_pct": _wilder_atr_pct(bars, _ATR_PERIOD),
        "vol_20": _return_stdev(closes, 20),
        "volume_ratio_20": _volume_ratio(volumes, 20),
    }
    return {name: _round(computed[name]) for name in names}


def _as_bars(prices) -> list[PriceBar]:
    # pandas 를 import 하지 않고 덕 타이핑으로 DataFrame 을 받는다 — 이 모듈이
    # base dependencies 만으로 돌아야 미래 참조 금지 테스트가 CI 에서 돈다.
    if hasattr(prices, "itertuples") and hasattr(prices, "columns"):
        rows = _rows_from_dataframe(prices)
    else:
        rows = list(prices)

    bars = [row if isinstance(row, PriceBar) else _bar_from_mapping(row) for row in rows]
    bars.sort(key=lambda bar: bar.trade_date)
    if len({bar.trade_date for bar in bars}) != len(bars):
        raise ValueError("같은 거래일이 두 번 들어왔다")
    return bars


def _rows_from_dataframe(frame) -> list[dict]:
    records = frame.to_dict("records")
    if "trade_date" in list(frame.columns):
        return records
    # 거래일이 컬럼이 아니면 인덱스에 있다고 본다.
    return [
        {**row, "trade_date": index_value}
        for index_value, row in zip(frame.index, records, strict=True)
    ]


def _bar_from_mapping(row: Mapping) -> PriceBar:
    missing = [key for key in ("trade_date", "high", "low", "close") if key not in row]
    if missing:
        raise ValueError(f"가격 행에 필요한 항목이 없다: {missing}")
    return PriceBar(
        trade_date=_as_date(row["trade_date"]),
        open=float(row.get("open", row["close"])),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row.get("volume", 0.0)),
    )


def _as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "date"):  # pandas Timestamp 등
        return value.date()
    raise TypeError(f"거래일로 쓸 수 없는 값이다: {value!r}")


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, _PRECISION)


def _return_over(closes: list[float], window: int) -> float | None:
    if len(closes) < window + 1:
        return None
    base = closes[-window - 1]
    if base <= 0:
        return None
    return closes[-1] / base - 1.0


def _ma_gap(closes: list[float], window: int) -> float | None:
    if len(closes) < window:
        return None
    average = math.fsum(closes[-window:]) / window
    if average <= 0:
        return None
    return closes[-1] / average - 1.0


def _return_stdev(closes: list[float], window: int) -> float | None:
    if len(closes) < window + 1:
        return None
    returns = []
    for previous, current in zip(closes[-window - 1 : -1], closes[-window:], strict=True):
        if previous <= 0:
            return None
        returns.append(current / previous - 1.0)
    mean = math.fsum(returns) / len(returns)
    variance = math.fsum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    return math.sqrt(variance)


def _volume_ratio(volumes: list[float], window: int) -> float | None:
    if len(volumes) < window:
        return None
    average = math.fsum(volumes[-window:]) / window
    if average <= 0:
        return None
    return volumes[-1] / average


def _wilder_rsi(closes: list[float], period: int) -> float | None:
    if len(closes) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for previous, current in zip(closes[:-1], closes[1:], strict=True):
        delta = current - previous
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    average_gain = math.fsum(gains[:period]) / period
    average_loss = math.fsum(losses[:period]) / period
    for index in range(period, len(gains)):
        average_gain = (average_gain * (period - 1) + gains[index]) / period
        average_loss = (average_loss * (period - 1) + losses[index]) / period

    if average_loss == 0.0:
        # 손실이 전혀 없으면 RS 가 무한대다. 완전 평탄(상승도 하락도 없음)은
        # 방향이 없다는 뜻이라 50, 상승만 있었으면 100 으로 둔다.
        return 100.0 if average_gain > 0.0 else 50.0
    relative_strength = average_gain / average_loss
    return 100.0 - 100.0 / (1.0 + relative_strength)


def _wilder_atr_pct(bars: list[PriceBar], period: int) -> float | None:
    if len(bars) < period + 1:
        return None
    true_ranges = [
        max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        )
        for previous, current in zip(bars[:-1], bars[1:], strict=True)
    ]

    average_true_range = math.fsum(true_ranges[:period]) / period
    for index in range(period, len(true_ranges)):
        average_true_range = (average_true_range * (period - 1) + true_ranges[index]) / period

    last_close = bars[-1].close
    if last_close <= 0:
        return None
    return average_true_range / last_close


def synthetic_series(
    start: date, closes: Iterable[float], *, volume: float = 1_000_000.0
) -> list[PriceBar]:
    """테스트·데모용 합성 시계열. 종가 목록만 주면 나머지를 채운다.

    거래일을 하루씩 늘리므로 실제 휴장일과는 무관하다 — 피처 계산은 달력이 아니라
    '행 순서'만 보기 때문에 회귀 테스트 목적에는 충분하다.
    """
    return [
        PriceBar(
            trade_date=start + timedelta(days=offset),
            open=close,
            high=close * 1.01,
            low=close * 0.99,
            close=close,
            volume=volume,
        )
        for offset, close in enumerate(closes)
    ]
