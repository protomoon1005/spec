# M3 판단 계층 <-> M4 백테스트 러너 접합 어댑터.
#
# 설명을 docstring이 아니라 주석에 두는 이유는 integrator.py·hedge.py 와 같다:
# scripts/check_asof_guard.py 가 문자열 리터럴(docstring 포함)에서 가드 대상
# 테이블명을 찾으면 CI가 실패한다. 주석은 AST에 안 잡힌다.
#
# ── 의존 방향은 단방향이다 ───────────────────────────────────────────
# 러너(app/backtest/)가 이 모듈을 알고, 이 모듈은 러너를 모른다. 여기서
# app.backtest 를 import 하면 bridge 테스트가 numpy·pandas·vectorbt 를 끌고
# 들어오는데, 그 셋은 backend/pyproject.toml 에 선언조차 안 되어 있어서
# (2026-09-13 통합 점검) CI 에서 이 테스트가 통째로 죽는다.
# 같은 이유로 pandas 도 import 하지 않는다 — 가격 입력은 T2 에서 만든 덕 타이핑
# 어댑터를 그대로 재사용한다.
#
# ── 무엇을 대체하는가 ────────────────────────────────────────────────
# 러너의 signal_from() 자리다. 하늘(M4)이 docstring 에 "M3(LightGBM)가 붙으면
# 이 함수가 그 보정 확률을 받는 자리" 라고 적어 둔 그 지점이고, 비중 산출·주문·
# 체결·성과·리포트는 전부 하늘 소유라 건드리지 않는다. 이 모듈이 내는 것은
# integrate() 의 IntegratedSignal 하나뿐이고, 그 signals 는 러너가 이미 쓰던
# s ∈ [-1, +1] 과 같은 규격이라 map_signals_to_weights 는 그대로 받는다.
#
# ── 워밍업 120 은 여기서 강제한다 ────────────────────────────────────
# Wilder 계열(rsi_14·atr_14_pct)은 기억이 무한해서 입력 이력 길이가 달라지면
# 같은 v0.1-ta9 로 다른 값이 나온다. 러너는 as_of 까지의 전체 이력을 넘기므로
# (하늘의 호출부를 고치지 않기 위해) **자르는 책임을 bridge 가 진다.**
# 이력이 FEATURE_WARMUP_ROWS 에 못 미치는 종목은 계산하지 않고 중립을 낸다 —
# 모자란 이력으로 계산한 값을 내보내면 백테스트 초기 구간이 후기와 다른 규칙으로
# 판정된다.
#
# ── 종가만으로는 못 만드는 피처가 있다 ───────────────────────────────
# 러너의 prices_wide 는 (날짜 x 종목) 종가 한 장이다. OHLC 도 거래량도 없다.
# 그런데 v0.1-ta9 의 atr_14_pct 는 고가·저가가, volume_ratio_20 은 거래량이
# 있어야 정의된다. H=L=C 로 채우면 TR 이 |dC| 로 접혀서 **ATR 이 아닌 값이
# atr_14_pct 라는 이름을 달고** 나간다. 그건 결측보다 나쁘다 — 모델이 그 값을
# 진짜 ATR 로 학습한다.
# 그래서 종가만 들어온 경우 이 둘을 명시적으로 None(결측) 으로 만든다.
# "0 으로 채우지 않는다"는 features.py 의 결측 방침을 그대로 따르는 것이고,
# 결측을 어떻게 다룰지는 스코어러 몫이다. OHLCV 가 갖춰져 들어오면 그대로 둔다.
#
# ── 스코어러 교체 지점은 SCORERS 한 곳이다 ───────────────────────────
# 세 관점 실물이 아직 하나도 없다(시장분석은 price_daily 대기, 감성은 Q2 대기,
# 온도는 etf_master 대기). 그때까지 계약 ② 의 고정 시드 목업으로 채운다.
# 실물이 나오면 SCORERS dict 의 한 줄만 바뀐다.
from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime

from app.contracts.integrated_signal import IntegratedSignal
from app.contracts.view_score import ViewScore, ViewType, mock_view_scores
from app.contracts.view_weights import VIEW_TYPES
from app.views.base import neutral_scores
from app.views.evidence import REASON_NO_DATA
from app.views.hedge import brier_loss, rolling_weights
from app.views.integrator import integrate
from app.views.market.features import (
    FEATURE_WARMUP_ROWS,
    PriceBar,
    compute_features,
)

logger = logging.getLogger(__name__)

# 종가만으로는 정의되지 않는 v0.1-ta9 피처. 위 주석의 근거로 결측 처리한다.
CLOSE_ONLY_UNAVAILABLE: tuple[str, ...] = ("atr_14_pct", "volume_ratio_20")

# 스코어러 출처 표시. 리포트를 보는 사람이 목업임을 알아야 한다.
MOCK_SOURCE = "mock"
REAL_SOURCE = "real"

# 관점 스코어러 한 개의 계약. 실물이 나오면 이 시그니처에 맞춰 끼운다.
# features 는 {종목: 피처 dict | None} 이고, None 은 워밍업 미달이라 bridge 가
# 이미 중립으로 채운 종목이다 (그런 종목은 tickers 에 들어오지 않는다).
ScorerFn = Callable[..., list[ViewScore]]


def _mock_scorer(view_type: ViewType) -> ScorerFn:
    # 계약 ② 의 목업을 스코어러 시그니처에 맞춘 얇은 껍데기다. 목업은 피처를
    # 보지 않고 (view_type, ticker, as_of) 해시로만 결정된다 — 그래서 재현성은
    # 있지만 가격과는 무관하다. 실물이 들어오면 features 를 쓰게 된다.
    def scorer(tickers: list[str], *, as_of: date, features: dict) -> list[ViewScore]:
        return mock_view_scores(view_type, tickers, as_of=as_of)

    scorer.scorer_source = MOCK_SOURCE  # type: ignore[attr-defined]
    return scorer


# ★ 실물 스코어러가 나오면 여기 한 줄씩만 바뀐다.
SCORERS: dict[str, ScorerFn] = {
    "market": _mock_scorer("market"),
    "sentiment": _mock_scorer("sentiment"),
    "regime": _mock_scorer("regime"),
}


def scorer_source(scorer: ScorerFn) -> str:
    return getattr(scorer, "scorer_source", REAL_SOURCE)


class BacktestJudge:
    # 백테스트 한 회차(run) 당 인스턴스 하나다. 손실 이력이 인스턴스 안에만
    # 쌓이므로 매 run 마다 새로 만들면 같은 입력이 같은 결과를 낸다 — 데모
    # 성공 기준이 그거다. 전역 상태를 두지 않는 이유이기도 하다.

    def __init__(
        self,
        tickers: Sequence[str],
        *,
        scorers: Mapping[str, ScorerFn] | None = None,
        warmup_rows: int = FEATURE_WARMUP_ROWS,
    ) -> None:
        unique = sorted({str(ticker) for ticker in tickers})
        if not unique:
            raise ValueError("종목이 하나도 없다")
        if warmup_rows < 1:
            raise ValueError(f"워밍업 행수는 1 이상이어야 한다: {warmup_rows!r}")

        chosen = dict(SCORERS if scorers is None else scorers)
        missing = sorted(set(VIEW_TYPES) - set(chosen))
        if missing:
            raise ValueError(f"스코어러가 없는 관점: {missing}")
        extra = sorted(set(chosen) - set(VIEW_TYPES))
        if extra:
            raise ValueError(f"알 수 없는 관점의 스코어러: {extra}")

        self._tickers = unique
        self._scorers = chosen
        self._warmup_rows = warmup_rows
        # 관점별 시간순 Brier 손실. rolling_weights 는 길이가 관점마다 같기를
        # 요구하므로 record_outcome 이 항상 세 관점에 한 개씩 같이 넣는다.
        self._losses: dict[str, list[float]] = {view_type: [] for view_type in VIEW_TYPES}
        self._pending: tuple[date, dict[str, dict[str, float]]] | None = None
        self.last_scores: list[ViewScore] = []

        mocked = sorted(vt for vt in VIEW_TYPES if scorer_source(self._scorers[vt]) == MOCK_SOURCE)
        if mocked:
            logger.warning(
                "판단 계층이 아직 목업이다 — 관점 %s 는 계약 2의 고정 시드 목업을 쓴다. "
                "백테스트 결과를 실력으로 읽지 마라",
                ", ".join(mocked),
            )

    @property
    def tickers(self) -> list[str]:
        return list(self._tickers)

    def scorer_sources(self) -> dict[str, str]:
        # 리포트/로그가 "이게 아직 목업인가"를 물을 수 있게 하는 자리다.
        return {view_type: scorer_source(self._scorers[view_type]) for view_type in VIEW_TYPES}

    def loss_history(self) -> dict[str, list[float]]:
        return {view_type: list(values) for view_type, values in self._losses.items()}

    def current_weights(self) -> dict[str, float]:
        # 손실 이력이 비면 전부 S_k = 0 이라 exp(0)=1 로 1/3 균등이 그대로 나온다.
        # FN-409 초기 가중치에 별도 분기가 없는 이유다 (hedge.py 주석 참고).
        return rolling_weights(self._losses)

    def features(self, prices, *, as_of) -> dict[str, dict[str, float | None] | None]:
        # 종목별 as_of 기준 피처. 워밍업 미달이면 None 이다.
        # ★ 러너가 전체 이력을 넘겨도 여기서 직전 warmup_rows 행만 잘라 쓴다.
        day = as_date(as_of)
        out: dict[str, dict[str, float | None] | None] = {}
        for ticker in self._tickers:
            bars, has_ohlc = _bars_for(prices, ticker, as_of=day)
            if len(bars) < self._warmup_rows:
                out[ticker] = None
                continue
            window = bars[-self._warmup_rows :]
            values = compute_features(window, as_of=day)
            if not has_ohlc:
                for name in CLOSE_ONLY_UNAVAILABLE:
                    values[name] = None
            out[ticker] = values
        return out

    def judge(self, prices, *, as_of) -> IntegratedSignal:
        day = as_date(as_of)
        features = self.features(prices, as_of=day)

        ready = [ticker for ticker in self._tickers if features[ticker] is not None]
        insufficient = [ticker for ticker in self._tickers if features[ticker] is None]

        scores: list[ViewScore] = []
        for view_type in VIEW_TYPES:
            if ready:
                produced = self._scorers[view_type](list(ready), as_of=day, features=features)
                scores.extend(produced)
            if insufficient:
                # 모자란 이력으로 계산한 값을 내보내지 않는다. 중립을 만드는 자리는
                # base.neutral_score 하나뿐이어야 한다 (views/base.py 주석).
                scores.extend(neutral_scores(view_type, list(insufficient), reason=REASON_NO_DATA))

        weights = self.current_weights()
        signal = integrate(scores, as_of=day, weights=weights)

        self.last_scores = scores
        # 다음 record_outcome 이 채점할 대상. calibrated_prob 만 남긴다 —
        # Brier 채점에 쓰는 값이 그거다 (raw_score 는 integrate 몫).
        self._pending = (
            day,
            {
                view_type: {
                    score.ticker: float(score.calibrated_prob)
                    for score in scores
                    if score.view_type == view_type
                }
                for view_type in VIEW_TYPES
            },
        )
        return signal

    def record_outcome(self, prices, *, as_of) -> None:
        # 직전 판단의 실현을 정산해 관점별 Brier 손실을 누적한다.
        # 러너가 실현을 알게 되는 지점에서 부른다. Brier 계산이 러너로 새지
        # 않게 하려고 한 줄짜리 호출로 뺀 것이다.
        #
        # 실현 방향은 포트폴리오 평가액이 아니라 **가격**에서 뽑는다. 러너의
        # pf.value() 는 vectorbt 실행이 끝난 뒤에야 나오므로 루프 안에서는 알 수
        # 없다 (2026-09-13 러너 확인). 관점이 맞혔는지는 종목이 올랐는지로
        # 채점하는 것이 원래 정의이기도 하다.
        if self._pending is None:
            return
        previous_day, probabilities = self._pending
        day = as_date(as_of)
        if day <= previous_day:
            # 아직 실현되지 않았다. 같은 시점을 두 번 채점하지 않는다.
            return

        outcomes: dict[str, int] = {}
        for ticker in self._tickers:
            before = _close_at(prices, ticker, on_or_before=previous_day)
            after = _close_at(prices, ticker, on_or_before=day)
            if before is None or after is None or before <= 0:
                continue
            if not all(ticker in probabilities[view_type] for view_type in VIEW_TYPES):
                continue
            outcomes[ticker] = 1 if after > before else 0

        self._pending = None
        if not outcomes:
            return

        graded = sorted(outcomes)
        for view_type in VIEW_TYPES:
            losses = [
                brier_loss(probabilities[view_type][ticker], outcomes[ticker]) for ticker in graded
            ]
            self._losses[view_type].append(math.fsum(losses) / len(losses))


# ── 입력 어댑터 (pandas 를 import 하지 않는다) ───────────────────────


def as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    if hasattr(value, "date"):  # pandas Timestamp 등
        return value.date()
    raise TypeError(f"거래일로 쓸 수 없는 값이다: {value!r}")


def _bars_for(prices, ticker: str, *, as_of: date) -> tuple[list[PriceBar], bool]:
    # 반환은 (as_of 이하 오름차순 PriceBar 목록, OHLCV 가 진짜인지).
    rows = _rows_for(prices, ticker)
    bars: list[PriceBar] = []
    has_ohlc = True
    for trade_date, row in rows:
        if trade_date > as_of:
            continue
        if isinstance(row, Mapping):
            close = float(row["close"])
            high = row.get("high")
            low = row.get("low")
            volume = row.get("volume")
            if high is None or low is None or volume is None:
                has_ohlc = False
            bars.append(
                PriceBar(
                    trade_date=trade_date,
                    open=float(row.get("open", close)),
                    high=float(close if high is None else high),
                    low=float(close if low is None else low),
                    close=close,
                    volume=float(0.0 if volume is None else volume),
                )
            )
        else:
            # 종가 하나만 들어온 경로. 러너의 prices_wide 가 여기다.
            has_ohlc = False
            close = float(row)
            bars.append(
                PriceBar(
                    trade_date=trade_date,
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    volume=0.0,
                )
            )
    bars.sort(key=lambda bar: bar.trade_date)
    return bars, has_ohlc


def _close_at(prices, ticker: str, *, on_or_before: date) -> float | None:
    best: tuple[date, float] | None = None
    for trade_date, row in _rows_for(prices, ticker):
        if trade_date > on_or_before:
            continue
        close = float(row["close"]) if isinstance(row, Mapping) else float(row)
        if best is None or trade_date > best[0]:
            best = (trade_date, close)
    return None if best is None else best[1]


def _rows_for(prices, ticker: str):
    # (거래일, 행) 을 흘린다. 행은 종가 스칼라이거나 OHLCV 매핑이다.
    #
    # 받는 형태 셋:
    #   1) (날짜 x 종목) 종가 표      — 러너의 prices_wide
    #   2) {종목: {날짜: 종가|행}}    — 테스트에서 쓰기 쉬운 형태
    #   3) {종목: [(날짜, 종가|행)]}  — 위와 같음
    if hasattr(prices, "columns") and hasattr(prices, "index"):
        if ticker not in list(prices.columns):
            return
        column = prices[ticker]
        for index_value, value in zip(list(prices.index), list(column), strict=True):
            if value is None or (isinstance(value, float) and math.isnan(value)):
                continue
            yield as_date(index_value), value
        return

    if not isinstance(prices, Mapping):
        raise TypeError(f"가격 입력을 해석할 수 없다: {type(prices)!r}")
    series = prices.get(ticker)
    if series is None:
        return
    if isinstance(series, Mapping):
        items = series.items()
    else:
        items = series
    for item in items:
        if isinstance(item, Mapping):
            yield as_date(item["trade_date"]), item
            continue
        trade_date, value = item
        yield as_date(trade_date), value
