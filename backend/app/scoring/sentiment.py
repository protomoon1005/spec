"""감성 관점 스코어러(빅카인즈 뉴스 감성) — bridge.ScorerFn 시그니처.

시장온도(app/scoring/regime.py)와 같은 틀이다. 국면 라벨 자리에 묶음별 뉴스 심리 상태
(app/views/sentiment/trend.py)가 들어간다. cutoff(schedule.cutoff_for)마다
P(사건 | 종목 묶음, 상태) 라플라스 평활 빈도표와 isotonic 보정기를 한 번 만들어 캐시한다.

표본: 원장 전 종목(상장폐지 포함)의 각 거래일 t 에 대해 (묶음 키, t 의 자기 자산군 상태, t+20행 사건).
가격 · 기사를 as_of=cutoff 로 읽으므로 실현일이 cutoff 뒤이거나 cutoff 15:30 뒤 기사는 없다.
판단일 상태는 판단일 as_of 로 다시 읽는다(최근 LOOKBACK_DAYS 만 — 120거래일 창에 충분하다).
기사가 2021-01 부터라 2021 전 cutoff 는 표가 없어 중립이다.

부호를 손으로 정하지 않는다. 부정 기사가 몰린 뒤 오르는지(역발상) 내리는지(모멘텀)는 빈도표가 정한다.
데이터가 모자란 날(기사 30건 미만 · 120거래일 커버리지 70% 미만 · 표 없음)은 neutral_score(no_data).
"""
from __future__ import annotations

import bisect
from collections.abc import Sequence
from datetime import date, timedelta

from app.contracts.view_score import ViewScore
from app.repositories import etf_master, news, price_daily
from app.scoring import schedule
from app.views.base import neutral_score, neutral_scores
from app.views.calibration import IsotonicCalibrator, fit_isotonic
from app.views.evidence import REASON_NO_DATA, sentiment_evidence
from app.views.market.labels import HORIZON, realized_outcome
from app.views.regime.scorer import event_rates, group_key
from app.views.sentiment import lens, trend

VIEW_TYPE = "sentiment"
MODEL_VERSION = f"{lens.LENS_ID}/{trend.STATE_RULE_VERSION}"
# 판단일 상태 조회 창(달력일). 120거래일 + 커버리지 계산 여유. 창 첫 세션은 일부만 차 있을 수 있지만
# 마지막 세션의 120거래일 창 밖이라 결과에 끼지 않는다.
LOOKBACK_DAYS = 400
TOP_ARTICLES = 3

_TABLES: dict[date, tuple[dict, IsotonicCalibrator] | None] = {}


def score(tickers: list[str], *, as_of: date, features: dict) -> list[ViewScore]:
    cutoff = schedule.cutoff_for(as_of)
    table = table_for(cutoff) if cutoff is not None else None
    if table is None:
        return neutral_scores(VIEW_TYPE, tickers, reason=REASON_NO_DATA)

    rates, calibrator = table
    calendar, points = load_points(as_of, since=as_of - timedelta(days=LOOKBACK_DAYS))
    current = {g: trend.StateLookup(p).point_at(as_of) for g, p in points.items()}
    evidence = {g: _evidence(g, point, calendar) for g, point in current.items() if point is not None}

    records = etf_master.get_by_tickers(tickers)
    out = []
    for ticker in tickers:
        record = records.get(ticker)
        group = record.asset_group_id if record else None
        point = current.get(group)
        key = group_key(group, name=record.name) if record else None
        raw = trend.raw_score(state=point.state if point else None, key=key, rates=rates)
        if raw is None:
            out.append(neutral_score(VIEW_TYPE, ticker, reason=REASON_NO_DATA))
            continue
        out.append(
            ViewScore(
                view_type=VIEW_TYPE,
                ticker=ticker,
                raw_score=raw,
                calibrated_prob=calibrator.predict(raw),
                evidence=evidence[group],
            )
        )
    return out


def _evidence(group: str, point: trend.TrendPoint, calendar: Sequence[date]) -> dict:
    i = bisect.bisect_left(calendar, point.session)
    after = calendar[i - 1] if i > 0 else None
    article_ids = news.get_top_articles(
        lens_id=lens.LENS_ID, sector=group, after=after, as_of=point.session, limit=TOP_ARTICLES
    )
    return dict(
        sentiment_evidence(
            article_ids=article_ids,
            lens_id=lens.LENS_ID,
            sector=group,
            model_version=MODEL_VERSION,
            n_articles=point.n,
            z=point.z,
            state=point.state,
            data_asof=point.session,
        )
    )


def load_points(
    as_of: date, *, since: date | None = None, edges: Sequence[float] = trend.ACTIVE_EDGES
) -> tuple[list[date], dict[str, list[trend.TrendPoint]]]:
    # (거래일 달력, {묶음: 세션 점 목록}). since 가 있으면 since 15:30 뒤 기사 · since 다음 날부터의 달력만.
    start = schedule.CALENDAR_START if since is None else since + timedelta(days=1)
    calendar = price_daily.get_trade_dates(start=start, as_of=as_of)
    rows = news.get_session_counts(
        lens_id=lens.LENS_ID, as_of=as_of, polarity_edge=lens.POLARITY_EDGE, since=since
    )
    points = {}
    for group in lens.GROUPS:
        sessions = trend.bucket_sessions(
            ((r.session, trend.SessionCount(r.n, r.n_pos, r.n_neg)) for r in rows if r.sector == group),
            calendar,
        )
        points[group] = trend.timeline(sessions, calendar, edges=edges)
    return calendar, points


def table_for(cutoff: date) -> tuple[dict, IsotonicCalibrator] | None:
    if cutoff not in _TABLES:
        _TABLES[cutoff] = build(cutoff)
    return _TABLES[cutoff]


def build(
    cutoff: date, *, edges: Sequence[float] = trend.ACTIVE_EDGES
) -> tuple[dict, IsotonicCalibrator] | None:
    # edges 인자는 상태 경계 측정(scripts/eval_sentiment_states.py)용이다. 운영 경로는 ACTIVE_EDGES.
    _, points = load_points(cutoff, edges=edges)
    lookups = {g: trend.StateLookup(p) for g, p in points.items()}

    records = etf_master.list_all(include_leveraged=True)
    meta = {
        r.ticker: (r.asset_group_id, group_key(r.asset_group_id, name=r.name))
        for r in records
        if r.asset_group_id in lookups
    }
    closes = price_daily.get_close_history([t for t, (_, k) in meta.items() if k], as_of=cutoff)

    states: dict[tuple[str, date], str | None] = {}
    samples = []  # (묶음 키, 상태, 사건)
    for ticker, series in closes.items():
        group, key = meta[ticker]
        for (day, before), (_, after) in zip(series, series[HORIZON:]):
            cell = (group, day)
            if cell not in states:
                point = lookups[group].point_at(day)
                states[cell] = point.state if point else None
            state = states[cell]
            if state is not None and before > 0:
                samples.append((key, state, realized_outcome(before, after)))
    if not samples:
        return None

    rates = event_rates(samples)
    # 시장온도와 같은 이유로 보정을 거친다 — 세 관점의 calibrated_prob 가 같은 뜻을 갖게.
    calibrator = fit_isotonic(
        [trend.raw_score(state=state, key=key, rates=rates) for key, state, _ in samples],
        [outcome for _, _, outcome in samples],
    )
    return rates, calibrator
