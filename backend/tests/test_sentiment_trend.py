"""감성 관점 순수 함수(app/views/sentiment/) — DB 없이 돈다."""
from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from app.views.evidence import sentiment_evidence
from app.views.sentiment import lens, trend
from app.views.sentiment.trend import SessionCount

D0 = date(2024, 1, 1)


def _cal(n: int) -> list[date]:
    return [D0 + timedelta(days=i) for i in range(n)]


def test_balance_is_log_ratio_with_plus_one():
    assert trend.balance(9, 0) == pytest.approx(math.log(10.0))
    assert trend.balance(0, 0) == 0.0
    with pytest.raises(ValueError):
        trend.balance(-1, 0)


def test_bucket_sessions_rolls_non_trading_days_forward():
    calendar = [date(2024, 1, 5), date(2024, 1, 8)]  # 금 · 월
    rows = [
        (date(2024, 1, 5), SessionCount(10, 2, 1)),
        (date(2024, 1, 6), SessionCount(3, 1, 0)),  # 토 -> 월
        (date(2024, 1, 7), SessionCount(4, 0, 2)),  # 일 -> 월
        (date(2024, 1, 8), SessionCount(5, 1, 1)),
        (date(2024, 1, 9), SessionCount(7, 7, 7)),  # 달력 뒤 -> 버림
    ]
    out = trend.bucket_sessions(rows, calendar)
    assert out == {date(2024, 1, 5): SessionCount(10, 2, 1), date(2024, 1, 8): SessionCount(12, 2, 3)}


def test_zscore_excludes_today_and_uses_previous_window():
    values = [float(i % 2) for i in range(120)] + [1.5]
    assert trend.zscores(values)[-1] == pytest.approx(2.0)


def test_zscore_is_clipped():
    values = [float(i % 2) for i in range(120)] + [100.0]
    assert trend.zscores(values)[-1] == trend.Z_CLIP


def test_coverage_gate_needs_seventy_percent_of_the_window():
    # 직전 120칸 중 84칸(70%) 이면 통과, 83칸이면 None.
    ok = [None] * 36 + [float(i % 2) for i in range(84)] + [0.5]
    short = [None] * 37 + [float(i % 2) for i in range(83)] + [0.5]
    assert trend.zscores(ok)[-1] is not None
    assert trend.zscores(short)[-1] is None
    # 창 밖(121칸 전) 값은 세지 않는다.
    assert trend.zscores([0.0] + short)[-1] is None


def test_zero_std_or_missing_today_is_none():
    assert trend.zscores([1.0] * 121)[-1] is None
    assert trend.zscores([float(i % 2) for i in range(120)] + [None])[-1] is None


@pytest.mark.parametrize(
    ("z", "three", "five"),
    [
        (-3.0, "fear", "extreme_fear"),
        (-1.5, "fear", "extreme_fear"),
        (-1.0, "fear", "fear"),
        (-0.99, "neutral", "fear"),
        (-0.5, "neutral", "fear"),
        (-0.49, "neutral", "neutral"),
        (0.0, "neutral", "neutral"),
        (0.5, "neutral", "greed"),
        (1.0, "greed", "greed"),
        (1.5, "greed", "extreme_greed"),
    ],
)
def test_state_buckets_are_symmetric(z, three, five):
    assert trend.state_of(z, edges=trend.EDGES_3) == three
    assert trend.state_of(z, edges=trend.EDGES_5) == five


def test_state_of_none_and_bad_edges():
    assert trend.state_of(None) is None
    with pytest.raises(ValueError):
        trend.state_of(0.0, edges=(1.0, -1.0))
    with pytest.raises(ValueError):
        trend.state_of(0.0, edges=(0.0,))


def test_timeline_marks_thin_sessions_missing():
    calendar = _cal(130)
    sessions = {d: SessionCount(40, 10 + i % 3, 10) for i, d in enumerate(calendar)}
    sessions[calendar[-1]] = SessionCount(29, 29, 0)  # 30건 미만 -> 결측
    points = trend.timeline(sessions, calendar)
    assert points[-1].index is None and points[-1].state is None
    assert points[-2].state is not None
    assert points[0].z is None  # 창이 차기 전


def test_lookup_uses_latest_session_and_staleness():
    calendar = _cal(130)
    sessions = {d: SessionCount(40, 10 + i % 3, 10) for i, d in enumerate(calendar)}
    lookup = trend.StateLookup(trend.timeline(sessions, calendar))
    last = calendar[-1]
    assert lookup.point_at(last).session == last
    assert lookup.point_at(last + timedelta(days=trend.MAX_STALE_DAYS)).session == last
    assert lookup.point_at(last + timedelta(days=trend.MAX_STALE_DAYS + 1)) is None
    assert lookup.point_at(calendar[50]) is None  # z 없음
    assert lookup.point_at(D0 - timedelta(days=1)) is None


def test_raw_score_uses_rates_and_none_for_unknown():
    rates = {("EQUITY", "fear"): 0.75}
    assert trend.raw_score(state="fear", key="EQUITY", rates=rates) == pytest.approx(0.5)
    assert trend.raw_score(state="greed", key="EQUITY", rates=rates) == 0.0
    assert trend.raw_score(state=None, key="EQUITY", rates=rates) is None
    assert trend.raw_score(state="fear", key=None, rates=rates) is None


def test_lens_mapping_and_labels():
    cats = ["경제>자원", "경제>증권_증시", "경제>유통", "경제>증권_증시"]
    assert lens.groups_for(cats, title="코스피 마감") == ("EQUITY", "COMMODITY")
    assert lens.groups_for(["경제>외환"], title="국고채 금리 하락") == ("BOND",)
    assert lens.groups_for(["경제>외환"], title="원 달러 환율 하락") == ()
    assert lens.groups_for(["경제>금융_재테크", "경제>증권_증시"], title="회사채 발행") == ("EQUITY", "BOND")
    assert lens.groups_for(["경제>유통"], title="국채 금리") == ()
    assert lens.groups_for(["", "사회>사회일반"], title="") == ()
    assert lens.label_of(lens.POLARITY_EDGE) == 1
    assert lens.label_of(-lens.POLARITY_EDGE) == -1
    assert lens.label_of(0.49) == 0
    assert lens.score_text(" 제목 ", "가" * 300) == "제목\n" + "가" * lens.BODY_CHARS


def test_sentiment_evidence_state_fields_are_all_or_nothing():
    built = sentiment_evidence(article_ids=[3, 1], lens_id="l", sector="EQUITY", model_version="m",
                               n_articles=40, z=-1.2, state="fear", data_asof=D0)
    assert built["data_asof"] == "2024-01-01" and built["z"] == -1.2 and built["n_articles"] == 40
    plain = sentiment_evidence(article_ids=[], lens_id="l", sector="X", model_version="m")
    assert "z" not in plain
    with pytest.raises(ValueError):
        sentiment_evidence(article_ids=[], lens_id="l", sector="X", model_version="m", z=1.0)
