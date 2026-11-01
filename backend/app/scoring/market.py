"""시장분석 관점(T3) 스코어러 — bridge.ScorerFn 시그니처.

as_of → cutoff(schedule.cutoff_for) → 그 cutoff 의 모델. 모델은 cutoff 로 모듈에
캐시한다. 격자가 실행과 무관하게 고정이라 같은 as_of 는 항상 같은 모델을 본다.

모델 출처: ml_models 에 그 버전이 등록돼 있으면 MinIO 에서 읽고, 없으면 그 자리에서
결정적으로 학습해 메모리에만 둔다(백테스트는 레지스트리에 쓰지 않는다 — 쓰는 건
train_model 태스크뿐이다). 어느 쪽이든 같은 데이터면 같은 모델이다.

학습 유니버스는 스펙 종목이 아니라 원장의 비레버리지 전체(상장폐지 포함 — 생존 편향 방지)다. 모델이 스펙과
무관해야 cutoff 캐시를 스펙 사이에 재사용할 수 있다.

추론 피처: bridge 가 넘긴 features 를 쓰되, 값이 None 인 항목은 저장소 일봉(OHLCV)으로
같은 규칙(직전 FEATURE_WARMUP_ROWS 행)으로 다시 계산해 채운다. 백테스트 러너는 종가만
넘겨서 atr_14_pct · volume_ratio_20 이 늘 None 인데, 모델은 진짜 OHLCV 로 학습했으므로
그대로 두면 학습 때 본 적 없는 결측 분기로 예측하게 된다.
"""
from __future__ import annotations

import logging
from datetime import date

from app.contracts.view_score import ViewScore
from app.repositories import etf_master, ml_models, price_daily
from app.scoring import model_store, schedule
from app.scoring.market_model import MarketModel, model_version, train
from app.views.base import neutral_score
from app.views.evidence import REASON_NO_DATA, market_evidence
from app.views.market.dataset import build_samples
from app.views.market.features import FEATURE_WARMUP_ROWS, PriceBar, compute_features

logger = logging.getLogger(__name__)

VIEW_TYPE = "market"
# 전체 이력을 받으려는 상한. 2019~ 일봉이 종목당 2천 행이 안 된다.
_ALL_ROWS = 100_000

_MODELS: dict[date, MarketModel | None] = {}
# (종목) -> {t: 피처}. t 의 피처는 과거 창만 보므로 cutoff 가 바뀌어도 그대로다.
_FEATURE_MEMO: dict[str, dict[date, dict[str, float | None]]] = {}


def score(tickers: list[str], *, as_of: date, features: dict) -> list[ViewScore]:
    cutoff = schedule.cutoff_for(as_of)
    model = model_for(cutoff) if cutoff is not None else None

    predictions = {}
    if model is not None:
        ready = [ticker for ticker in tickers if features.get(ticker) is not None]
        if ready:
            rows = [_fill_missing(ticker, features[ticker], as_of=as_of) for ticker in ready]
            predictions = dict(zip(ready, model.predict(rows), strict=True))

    out = []
    for ticker in tickers:
        prediction = predictions.get(ticker)
        if prediction is None:
            out.append(neutral_score(VIEW_TYPE, ticker, reason=REASON_NO_DATA))
            continue
        p = prediction.prob_calibrated
        out.append(
            ViewScore(
                view_type=VIEW_TYPE,
                ticker=ticker,
                raw_score=2.0 * p - 1.0,
                calibrated_prob=p,
                evidence=dict(
                    market_evidence(
                        shap=prediction.shap,
                        prob_raw=prediction.prob_raw,
                        prob_calibrated=p,
                        model_version=model.version,
                    )
                ),
            )
        )
    return out


def model_for(cutoff: date) -> MarketModel | None:
    if cutoff in _MODELS:
        return _MODELS[cutoff]
    record = ml_models.get_model(model_version(cutoff))
    if record is not None and record.artifact_uri:
        model = MarketModel.loads(model_store.download(record.artifact_uri))
    else:
        model = train_for(cutoff)
        if model is None:
            logger.info("시장분석 %s: 표본이 모자라 학습하지 않는다 — 중립", cutoff)
    _MODELS[cutoff] = model
    return model


def train_for(cutoff: date) -> MarketModel | None:
    """cutoff 이하 일봉으로 학습 표본을 만들어 학습한다. 레지스트리에 쓰지 않는다."""
    samples = []
    for record in etf_master.list_all(include_leveraged=False):
        bars = _bars(record.ticker, as_of=cutoff, rows=_ALL_ROWS)
        memo = _FEATURE_MEMO.setdefault(record.ticker, {})
        samples.extend((record.ticker, s) for s in build_samples(bars, cutoff=cutoff, memo=memo))
    return train(samples, cutoff=cutoff)


def _fill_missing(ticker: str, values: dict, *, as_of: date) -> dict:
    if all(value is not None for value in values.values()):
        return values
    bars = _bars(ticker, as_of=as_of, rows=FEATURE_WARMUP_ROWS)
    if len(bars) < FEATURE_WARMUP_ROWS:
        return values
    full = compute_features(bars, as_of=as_of)
    return {name: full.get(name) if value is None else value for name, value in values.items()}


def _bars(ticker: str, *, as_of: date, rows: int) -> list[PriceBar]:
    return [
        PriceBar(
            trade_date=row["trade_date"],
            open=row["open"],
            high=row["high"],
            low=row["low"],
            close=row["close"],
            volume=row["volume"],
        )
        for row in price_daily.get_price_window(ticker, as_of=as_of, lookback_days=rows)
    ]
