"""시장온도 관점(T4b) 스코어러 — bridge.ScorerFn 시그니처.

그날 국면(KOSPI200 추세 기준 스냅샷)을 종목 묶음(자산군·인버스)별 사건 빈도로 펴고
(app/views/regime/scorer.py), isotonic 으로 보정한다. 빈도표와 보정기는 cutoff
(schedule.cutoff_for)마다 한 번 만들어 모듈에 캐시한다 — 격자가 실행과 무관해서
같은 as_of 는 언제나 같은 표를 본다.

표본: 원장 전 종목(상장폐지 포함, 스펙 무관)의 각 거래일 t 에 대해 (묶음, t 의 국면, t+20행 사건).
가격·국면을 as_of=cutoff 로 읽으므로 실현일이 cutoff 뒤인 표본은 애초에 없다.
cutoff 하나에 0.2초 남짓이라(2019~2025, 76종목) 증분 캐시는 두지 않는다.
"""
from __future__ import annotations

from datetime import date

from app.contracts.view_score import ViewScore
from app.repositories import etf_master, price_daily, regime
from app.scoring import schedule
from app.views.base import neutral_score, neutral_scores
from app.views.calibration import IsotonicCalibrator, fit_isotonic
from app.views.evidence import REASON_NO_DATA, regime_evidence
from app.views.market.labels import HORIZON, realized_outcome
from app.views.regime.judge import LABEL_UNKNOWN
from app.views.regime.scorer import event_rates, group_key, raw_score

VIEW_TYPE = "regime"
TREND_INDEX = "KOSPI200"

_TABLES: dict[date, tuple[dict, IsotonicCalibrator] | None] = {}


def score(tickers: list[str], *, as_of: date, features: dict) -> list[ViewScore]:
    snapshot = regime.get_regime_snapshot(TREND_INDEX, as_of=as_of)
    cutoff = schedule.cutoff_for(as_of)
    table = table_for(cutoff) if cutoff is not None else None
    if snapshot is None or snapshot.regime_label == LABEL_UNKNOWN or table is None:
        return neutral_scores(VIEW_TYPE, tickers, reason=REASON_NO_DATA)

    rates, calibrator = table
    records = etf_master.get_by_tickers(tickers)
    evidence = dict(
        regime_evidence(
            trend_index=TREND_INDEX,
            regime_label=snapshot.regime_label,
            intensity=float(snapshot.intensity or 0.0),
            threshold_state=snapshot.threshold_state or {},
        )
    )
    out = []
    for ticker in tickers:
        record = records.get(ticker)
        key = group_key(record.asset_group_id, name=record.name) if record else None
        raw = raw_score(regime_label=snapshot.regime_label, key=key, rates=rates)
        if raw is None:
            out.append(neutral_score(VIEW_TYPE, ticker, reason=REASON_NO_DATA))
            continue
        out.append(
            ViewScore(
                view_type=VIEW_TYPE,
                ticker=ticker,
                raw_score=raw,
                calibrated_prob=calibrator.predict(raw),
                evidence=evidence,
            )
        )
    return out


def table_for(cutoff: date) -> tuple[dict, IsotonicCalibrator] | None:
    if cutoff not in _TABLES:
        _TABLES[cutoff] = _build(cutoff)
    return _TABLES[cutoff]


def _build(cutoff: date) -> tuple[dict, IsotonicCalibrator] | None:
    labels = {
        snap.as_of: snap.regime_label
        for snap in regime.get_regime_history(TREND_INDEX, as_of=cutoff)
        if snap.regime_label != LABEL_UNKNOWN
    }
    records = etf_master.list_all(include_leveraged=True)
    keys = {r.ticker: group_key(r.asset_group_id, name=r.name) for r in records}
    closes = price_daily.get_close_history([t for t, k in keys.items() if k], as_of=cutoff)

    samples = []  # (묶음, 국면, 사건)
    for ticker, series in closes.items():
        for (day, before), (_, after) in zip(series, series[HORIZON:]):
            label = labels.get(day)
            if label is not None and before > 0:
                samples.append((keys[ticker], label, realized_outcome(before, after)))
    if not samples:
        return None

    rates = event_rates(samples)
    # 빈도 자체가 확률이라 보정은 대개 항등에 가깝다. 그래도 거친다 — 시장분석과 같은
    # 단조 보정 단계를 지나야 두 관점의 calibrated_prob 가 같은 뜻을 갖는다.
    calibrator = fit_isotonic(
        [raw_score(regime_label=label, key=key, rates=rates) for key, label, _ in samples],
        [outcome for _, _, outcome in samples],
    )
    return rates, calibrator
