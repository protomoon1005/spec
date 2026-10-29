"""감성 관점 스코어러(app/scoring/sentiment.py) — 저장소 함수 자리에 합성 데이터를 넣는다."""
from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.repositories.news import SessionCountRow
from app.scoring import sentiment as scoring
from app.views.sentiment import trend

# 거래일 400개(달력일로 둔다). EQUITY 묶음: 200~259 세션에 부정 기사 급증, 그 뒤 주식 상승.
DAYS = [date(2023, 1, 1) + timedelta(days=i) for i in range(400)]


def _rows(as_of: date, since: date | None):
    out = []
    for i, d in enumerate(DAYS):
        if d > as_of or (since is not None and d <= since):
            continue
        n_neg = 30 if 200 <= i < 260 else 5 + i % 4
        out.append(SessionCountRow(d, "EQUITY", 50, 10 + i % 3, n_neg, 0.0))
        out.append(SessionCountRow(d, "BOND", 50, 10 + i % 3, 5 + i % 4, 0.0))
        out.append(SessionCountRow(d, "COMMODITY", 10, 5, 5, 0.0))  # 늘 30건 미만
    return out


def _closes(kind: str) -> list[float]:
    price, out = 100.0, []
    for i in range(len(DAYS)):
        if kind == "EQ":
            price *= 1.01 if 210 <= i < 300 else (0.999 if i % 2 else 1.0005)
        elif kind == "INV":
            price *= 0.99 if 210 <= i < 300 else (1.001 if i % 2 else 0.9995)
        else:
            price *= 0.999
        out.append(price)
    return out


CLOSES = {"EQ": _closes("EQ"), "INV": _closes("INV"), "BD": _closes("BD"), "GD": _closes("BD")}
RECORDS = {
    "EQ": SimpleNamespace(ticker="EQ", name="주식", asset_group_id="EQUITY"),
    "INV": SimpleNamespace(ticker="INV", name="KODEX 인버스", asset_group_id="EQUITY"),
    "BD": SimpleNamespace(ticker="BD", name="채권", asset_group_id="BOND"),
    "GD": SimpleNamespace(ticker="GD", name="금", asset_group_id="COMMODITY"),
}


@pytest.fixture(autouse=True)
def synthetic(monkeypatch):
    scoring._TABLES.clear()
    reads = []

    def counts(*, lens_id, as_of, polarity_edge, since=None):
        reads.append(as_of)
        return _rows(as_of, since)

    monkeypatch.setattr(scoring.news, "get_session_counts", counts)
    monkeypatch.setattr(scoring.news, "get_top_articles", lambda **kw: [7, 8, 9])
    monkeypatch.setattr(scoring.price_daily, "get_trade_dates",
                        lambda *, start, as_of: [d for d in DAYS if start <= d <= as_of])
    monkeypatch.setattr(scoring.etf_master, "list_all", lambda **_: list(RECORDS.values()))
    monkeypatch.setattr(
        scoring.etf_master, "get_by_tickers", lambda ts: {t: RECORDS[t] for t in ts if t in RECORDS}
    )
    monkeypatch.setattr(
        scoring.price_daily,
        "get_close_history",
        lambda tickers, *, as_of: {
            t: [(d, CLOSES[t][i]) for i, d in enumerate(DAYS) if d <= as_of] for t in tickers
        },
    )
    monkeypatch.setattr(scoring.schedule, "cutoff_for", lambda as_of: as_of)
    monkeypatch.setattr(scoring.schedule, "CALENDAR_START", DAYS[0])
    yield reads
    scoring._TABLES.clear()


def test_table_sign_comes_from_data_and_inverse_flips():
    rates, _ = scoring.table_for(DAYS[340])
    assert rates[("EQUITY", "fear")] > 0.5 > rates[("EQUITY_INVERSE", "fear")]


def test_scores_in_a_fear_session(monkeypatch):
    as_of = DAYS[340]
    scoring.table_for(as_of)  # 표는 실제 상태로 먼저 만든다
    fear = trend.TrendPoint(DAYS[340], 50, -1.0, -2.0, "fear")
    monkeypatch.setattr(trend.StateLookup, "point_at", lambda self, day: fear)
    scores = {s.ticker: s for s in scoring.score(["EQ", "INV"], as_of=as_of, features={})}
    assert scores["EQ"].raw_score > 0 > scores["INV"].raw_score
    evidence = scores["EQ"].evidence
    assert evidence["article_ids"] == [7, 8, 9] and evidence["sector"] == "EQUITY"
    assert evidence["state"] == "fear" and evidence["n_articles"] == 50
    assert evidence["data_asof"] == DAYS[340].isoformat()


def test_thin_group_and_unknown_ticker_are_neutral():
    scores = {s.ticker: s for s in scoring.score(["GD", "ZZ", "EQ"], as_of=DAYS[340], features={})}
    for ticker in ("GD", "ZZ"):
        assert (scores[ticker].raw_score, scores[ticker].calibrated_prob) == (0.0, 0.5)
        assert scores[ticker].evidence == {"skipped": True, "reason": "no_data"}
    assert "state" in scores["EQ"].evidence


def test_no_articles_is_neutral(monkeypatch):
    monkeypatch.setattr(scoring.news, "get_session_counts", lambda **_: [])
    scores = scoring.score(["EQ", "BD"], as_of=DAYS[340], features={})
    assert all(s.evidence == {"skipped": True, "reason": "no_data"} for s in scores)


def test_same_as_of_twice_and_window_equals_full_history():
    first = scoring.score(["EQ", "BD"], as_of=DAYS[340], features={})
    scoring._TABLES.clear()
    assert scoring.score(["EQ", "BD"], as_of=DAYS[340], features={}) == first
    # 판단일 조회 창(LOOKBACK_DAYS)으로 구한 상태 = 전체 이력으로 구한 상태
    _, windowed = scoring.load_points(DAYS[340], since=DAYS[340] - timedelta(days=scoring.LOOKBACK_DAYS))
    _, full = scoring.load_points(DAYS[340])
    for group in ("EQUITY", "BOND"):
        assert windowed[group][-1] == full[group][-1]


def test_reads_nothing_after_as_of(synthetic):
    scoring.score(["EQ"], as_of=DAYS[300], features={})
    assert set(synthetic) == {DAYS[300]}


def test_warmup_is_neutral():
    [score] = scoring.score(["EQ"], as_of=DAYS[100], features={})
    assert score.evidence["skipped"] is True
