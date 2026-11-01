"""시장온도 관점(T4b) — 순수 규칙(app/views/regime/scorer.py)과 스코어러(app/scoring/regime.py)."""
from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.scoring import regime as scoring
from app.views.regime.judge import LABEL_RISK_OFF, LABEL_RISK_ON, LABEL_UNKNOWN
from app.views.regime.scorer import event_rates, group_key, raw_score

# ── 순수 규칙 ────────────────────────────────────────────────────────


def test_group_key_splits_inverse_and_rejects_unknown_groups():
    assert group_key("EQUITY", name="KODEX 200") == "EQUITY"
    assert group_key("EQUITY", name="KODEX 인버스") == "EQUITY_INVERSE"
    assert group_key(None, name="x") is None
    assert group_key("SECTOR_OTHER", name="x") is None


def test_event_rates_are_laplace_smoothed():
    rates = event_rates([("EQUITY", LABEL_RISK_ON, 1)] * 3 + [("EQUITY", LABEL_RISK_ON, 0)])
    assert rates[("EQUITY", LABEL_RISK_ON)] == pytest.approx(4 / 6)


def test_raw_score_is_two_p_minus_one_and_half_for_unseen_cells():
    rates = {("BOND", LABEL_RISK_OFF): 0.75}
    assert raw_score(regime_label=LABEL_RISK_OFF, key="BOND", rates=rates) == pytest.approx(0.5)
    assert raw_score(regime_label=LABEL_RISK_ON, key="BOND", rates=rates) == 0.0


def test_unknown_regime_or_group_cannot_be_judged():
    rates = {("EQUITY", LABEL_RISK_ON): 0.9}
    assert raw_score(regime_label=LABEL_UNKNOWN, key="EQUITY", rates=rates) is None
    assert raw_score(regime_label=LABEL_RISK_ON, key=None, rates=rates) is None


# ── 스코어러 (저장소 함수 자리에 합성 데이터를 넣는다) ─────────────────

DAYS = [date(2024, 1, 1) + timedelta(days=i) for i in range(120)]
# 앞 60일 risk_on 에서 주식 상승, 뒤 60일 risk_off 에서 주식 하락. 채권은 반대.
LABELS = {d: (LABEL_RISK_ON if i < 60 else LABEL_RISK_OFF) for i, d in enumerate(DAYS)}


def _closes(direction: float) -> list[float]:
    price, out = 100.0, []
    for i in range(len(DAYS)):
        price *= 1 + (direction if i < 70 else -direction)
        out.append(price)
    return out


CLOSES = {"EQ": _closes(0.01), "BD": _closes(-0.01)}
RECORDS = {
    "EQ": SimpleNamespace(ticker="EQ", name="주식", asset_group_id="EQUITY"),
    "BD": SimpleNamespace(ticker="BD", name="채권", asset_group_id="BOND"),
}


@pytest.fixture(autouse=True)
def synthetic(monkeypatch):
    scoring._TABLES.clear()
    reads = []

    def history(trend_index, *, as_of):
        reads.append(as_of)
        return [SimpleNamespace(as_of=d, regime_label=label) for d, label in LABELS.items() if d <= as_of]

    def snapshot(trend_index, *, as_of):
        day = max(d for d in LABELS if d <= as_of)
        return SimpleNamespace(regime_label=LABELS[day], intensity=0.5, threshold_state={})

    monkeypatch.setattr(scoring.regime, "get_regime_history", history)
    monkeypatch.setattr(scoring.regime, "get_regime_snapshot", snapshot)
    monkeypatch.setattr(scoring.etf_master, "list_all", lambda **_: list(RECORDS.values()))
    monkeypatch.setattr(scoring.etf_master, "get_by_tickers", lambda ts: {t: RECORDS[t] for t in ts})
    monkeypatch.setattr(
        scoring.price_daily,
        "get_close_history",
        lambda tickers, *, as_of: {
            t: [(d, CLOSES[t][i]) for i, d in enumerate(DAYS) if d <= as_of] for t in tickers
        },
    )
    monkeypatch.setattr(scoring.schedule, "cutoff_for", lambda as_of: as_of)
    yield reads
    scoring._TABLES.clear()


def test_scores_follow_the_regime_conditional_frequency():
    scores = {s.ticker: s for s in scoring.score(["EQ", "BD"], as_of=DAYS[100], features={})}
    # risk_off 표본(60~79일)은 주식 하락이 대부분이다.
    assert scores["EQ"].raw_score < 0 < scores["BD"].raw_score
    assert scores["EQ"].calibrated_prob < 0.5 < scores["BD"].calibrated_prob
    assert scores["EQ"].evidence["regime_label"] == LABEL_RISK_OFF


def test_same_as_of_twice_gives_the_same_scores():
    first = scoring.score(["EQ", "BD"], as_of=DAYS[100], features={})
    scoring._TABLES.clear()
    assert scoring.score(["EQ", "BD"], as_of=DAYS[100], features={}) == first


def test_table_reads_nothing_after_the_cutoff(synthetic):
    scoring.score(["EQ"], as_of=DAYS[50], features={})
    assert synthetic == [DAYS[50]]


def test_unknown_ticker_is_neutral(monkeypatch):
    monkeypatch.setattr(scoring.etf_master, "get_by_tickers", lambda ts: {})
    [score] = scoring.score(["ZZ"], as_of=DAYS[100], features={})
    assert (score.raw_score, score.calibrated_prob) == (0.0, 0.5)
    assert score.evidence["skipped"] is True
