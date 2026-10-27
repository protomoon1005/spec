"""라이브 판단 결선 (T6 나머지 + T10) — app/scoring/live.py.

포트폴리오 FK 체인은 conftest 의 portfolio_id 픽스처를 쓰고, 가격은 합성 시계열을
저장소 함수 자리에 주입한다(가격 백필 없이 돈다). 스코어러는 결정적 가짜로 바꾼다.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.contracts.view_score import ViewScore
from app.contracts.view_weights import WEIGHT_FLOOR
from app.repositories.view_weights import get_view_weights
from app.scoring import live
from app.views import bridge
from app.views.base import neutral_scores
from app.views.evidence import REASON_NO_DATA

TICKERS = ["069500", "148070"]
START = date(2024, 1, 1)
DAYS = [START + timedelta(days=i) for i in range(400)]
# 069500 은 계속 오르고 148070 은 계속 내린다 — 실현 방향이 결정적이다.
CLOSES = {
    "069500": [100.0 + i for i in range(len(DAYS))],
    "148070": [500.0 - i for i in range(len(DAYS))],
}


def _bar(ticker: str, i: int) -> dict:
    close = CLOSES[ticker][i]
    return {"trade_date": DAYS[i], "open": close, "high": close + 1, "low": close - 1,
            "close": close, "volume": 1000.0}


def _fake(view_type: str):
    # market 은 069500 상승·148070 하락을 맞히고, regime 은 반대로 틀린다.
    def scorer(tickers, *, as_of, features):
        if view_type == "sentiment":
            return neutral_scores(view_type, list(tickers), reason=REASON_NO_DATA)
        right = 1.0 if view_type == "market" else -1.0
        return [
            ViewScore(
                view_type=view_type,
                ticker=ticker,
                raw_score=right * (0.8 if ticker == "069500" else -0.8),
                calibrated_prob=0.5 + right * (0.4 if ticker == "069500" else -0.4),
                evidence={"source": "fake"},
            )
            for ticker in tickers
        ]

    return scorer


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    monkeypatch.setattr(bridge, "SCORERS", {vt: _fake(vt) for vt in ("market", "sentiment", "regime")})
    monkeypatch.setattr(live, "_portfolio_tickers", lambda pid: (None, TICKERS))

    def window(ticker, *, as_of, lookback_days):
        rows = [_bar(ticker, i) for i, d in enumerate(DAYS) if d <= as_of]
        return rows[-lookback_days:]

    monkeypatch.setattr(live.price_daily, "get_price_window", window)
    monkeypatch.setattr(
        live.price_daily, "get_trade_dates", lambda *, start, as_of: [d for d in DAYS if start <= d <= as_of]
    )
    monkeypatch.setattr(
        live.price_daily,
        "get_close_history",
        lambda tickers, *, as_of: {
            t: [(d, CLOSES[t][i]) for i, d in enumerate(DAYS) if d <= as_of] for t in tickers
        },
    )


@pytest.fixture
def portfolio(engine, portfolio_id, monkeypatch):
    with engine.connect() as conn:
        spec_id = conn.execute(
            text("SELECT spec_id FROM portfolios WHERE portfolio_id = :pid"), {"pid": portfolio_id}
        ).scalar_one()
    monkeypatch.setattr(live, "_portfolio_tickers", lambda pid: (spec_id, TICKERS))
    yield portfolio_id
    # conftest 정리가 portfolios 를 지우기 전에 FK 로 걸린 판단 기록부터 지운다.
    with engine.begin() as conn:
        for table in ("view_performance", "view_scores"):
            conn.execute(
                text(
                    f"DELETE FROM {table} WHERE decision_id IN "
                    "(SELECT decision_id FROM decision_records WHERE portfolio_id = :pid)"
                ),
                {"pid": portfolio_id},
            )
        conn.execute(text("DELETE FROM decision_records WHERE portfolio_id = :pid"), {"pid": portfolio_id})


def test_initial_weights_are_uniform():
    assert live.initial_weights() == pytest.approx({"market": 1 / 3, "sentiment": 1 / 3, "regime": 1 / 3})


def test_same_portfolio_and_as_of_gives_the_same_signal(portfolio):
    first = live.daily_judge(portfolio, as_of=DAYS[200])
    second = live.daily_judge(portfolio, as_of=DAYS[200])
    assert first["signal"] == second["signal"]
    assert first["decision_id"] != second["decision_id"]


def test_without_weight_history_the_judgement_uses_fn409_uniform(portfolio):
    result = live.daily_judge(portfolio, as_of=DAYS[200])
    assert result["signal"]["view_weights_used"] == pytest.approx(live.initial_weights())


def test_weights_written_on_as_of_are_not_used_that_day(portfolio):
    live.init_view_weights(portfolio, as_of=DAYS[200])
    assert get_view_weights(portfolio, as_of=DAYS[200]) == {}
    assert get_view_weights(portfolio, as_of=DAYS[201]) == pytest.approx(live.initial_weights())


def test_only_decisions_realized_before_as_of_are_graded(portfolio):
    for i in (150, 160, 170):
        live.daily_judge(portfolio, as_of=DAYS[i])
    # 판단일 150 의 실현일은 170. as_of=171 이면 그 하나만 확정(< as_of)이다.
    assert len(live.grade_decisions(portfolio, as_of=DAYS[171])) == 1
    assert len(live.grade_decisions(portfolio, as_of=DAYS[200])) == 3


def test_grading_is_idempotent(engine, portfolio):
    live.daily_judge(portfolio, as_of=DAYS[150])
    live.grade_decisions(portfolio, as_of=DAYS[200])
    live.grade_decisions(portfolio, as_of=DAYS[201])
    with engine.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM view_performance WHERE portfolio_id = :pid"), {"pid": portfolio}
        ).scalar_one()
    assert count == 3  # 판단 하나 x 관점 셋


def test_right_view_gains_weight_and_wrong_view_stays_above_the_floor(portfolio):
    for i in range(100, 160):
        live.daily_judge(portfolio, as_of=DAYS[i])
    result = live.update_view_weights(portfolio, as_of=DAYS[200])
    weights = result["weights"]

    assert sum(weights.values()) == pytest.approx(1.0, abs=1e-9)
    # 60개 판단 내내 market 은 맞히고(Brier 0.01) regime 은 틀린다(0.81). 중립 감성(0.25)과
    # regime 은 둘 다 하한에 걸린다 — water-filling 이 하한을 지킨다.
    assert weights["market"] == pytest.approx(1 - 2 * WEIGHT_FLOOR)
    assert weights["sentiment"] == pytest.approx(WEIGHT_FLOOR)
    assert weights["regime"] == pytest.approx(WEIGHT_FLOOR)
    assert get_view_weights(portfolio, as_of=DAYS[201]) == pytest.approx(weights)


def test_hit_rate_and_contribution_follow_the_realized_direction(engine, portfolio):
    live.daily_judge(portfolio, as_of=DAYS[150])
    live.grade_decisions(portfolio, as_of=DAYS[200])
    with engine.connect() as conn:
        rows = {
            row.view_type: (
                None if row.hit_rate is None else float(row.hit_rate),
                float(row.contribution),
            )
            for row in conn.execute(
                text(
                    "SELECT view_type, hit_rate, contribution FROM view_performance"
                    " WHERE portfolio_id = :pid"
                ),
                {"pid": portfolio},
            )
        }
    assert rows["market"] == (1, pytest.approx(0.8))
    assert rows["regime"] == (0, pytest.approx(-0.8))
    assert rows["sentiment"] == (None, 0)


def test_decision_records_the_model_version_the_scores_came_from(engine, portfolio, monkeypatch):
    def market(tickers, *, as_of, features):
        return [
            ViewScore(view_type="market", ticker=t, raw_score=0.2, calibrated_prob=0.6,
                      evidence={"model_version": "market-lgbm-test"})
            for t in tickers
        ]

    monkeypatch.setitem(bridge.SCORERS, "market", market)
    decision_id = live.daily_judge(portfolio, as_of=DAYS[200])["decision_id"]
    with engine.connect() as conn:
        version = conn.execute(
            text("SELECT model_version FROM decision_records WHERE decision_id = :id"), {"id": decision_id}
        ).scalar_one()
    assert version == "market=market-lgbm-test,sentiment=real,regime=real"


# ── 매일 루프 · 시계 하나 (2026-10-21) ────────────────────────────────


def _activate_on(engine, portfolio_id: int, day: date) -> None:
    # 활성화 시각을 그날 KST 10:00 으로 둔다. 승인 API 는 now() 를 쓰므로 여기서만 고정한다.
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE portfolios"
                " SET activated_at = (CAST(:d AS date) + time '10:00') AT TIME ZONE 'Asia/Seoul'"
                " WHERE portfolio_id = :pid"
            ),
            {"d": day, "pid": portfolio_id},
        )


def _live_decisions(engine, portfolio_id: int) -> int:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT count(*) FROM decision_records WHERE portfolio_id = :pid"), {"pid": portfolio_id}
        ).scalar_one()


def test_live_records_before_the_activation_day_are_refused(engine, portfolio):
    # 데이터 시계(가격 날짜)로 갱신을 쌓으면 활성화일의 1/3 이 그 갱신을 가린다 — 아예 못 남긴다
    _activate_on(engine, portfolio, DAYS[200])
    live.init_view_weights(portfolio, as_of=DAYS[200])

    with pytest.raises(ValueError, match="활성화일"):
        live.daily_judge(portfolio, as_of=DAYS[150])
    with pytest.raises(ValueError, match="활성화일"):
        live.update_view_weights(portfolio, as_of=DAYS[150])
    assert _live_decisions(engine, portfolio) == 0


def test_live_day_on_the_activation_day_keeps_fn409_and_judges_once(engine, portfolio):
    _activate_on(engine, portfolio, DAYS[200])
    live.init_view_weights(portfolio, as_of=DAYS[200])

    first = live._live_day(portfolio, as_of=DAYS[200])
    again = live._live_day(portfolio, as_of=DAYS[200])

    # 활성화일 가중치는 FN-409 1/3 그대로다(충돌 없이 건너뜀). 판단은 한 번만 쌓인다
    assert first["weights"] == "skipped" and isinstance(first["judge"], int)
    assert again == {"weights": "skipped", "judge": "skipped"}
    assert _live_decisions(engine, portfolio) == 1
    assert get_view_weights(portfolio, as_of=DAYS[201]) == pytest.approx(live.initial_weights())


def test_live_day_after_activation_updates_weights_then_judges(engine, portfolio):
    _activate_on(engine, portfolio, DAYS[200])
    live.init_view_weights(portfolio, as_of=DAYS[200])

    result = live._live_day(portfolio, as_of=DAYS[201])

    assert sum(result["weights"].values()) == pytest.approx(1)
    assert isinstance(result["judge"], int)
    # 오늘 쓴 가중치는 오늘 판단에 못 쓴다(strict <) — 오늘 판단은 활성화일 1/3 을 읽는다
    with engine.connect() as conn:
        used = conn.execute(
            text("SELECT view_weights_used FROM decision_records WHERE decision_id = :d"),
            {"d": result["judge"]},
        ).scalar_one()
    assert used == pytest.approx(live.initial_weights())


def test_run_live_day_uses_the_wall_clock_and_isolates_failures(engine, portfolio, monkeypatch):
    _activate_on(engine, portfolio, DAYS[200])
    live.init_view_weights(portfolio, as_of=DAYS[200])
    monkeypatch.setattr(live, "live_today", lambda: DAYS[201])
    monkeypatch.setattr(live.portfolios, "list_active", lambda: [-1, portfolio])

    out = live.run_live_day()

    assert out["as_of"] == DAYS[201].isoformat()
    assert [p["portfolio_id"] for p in out["portfolios"]] == [portfolio]
    assert [f["portfolio_id"] for f in out["failed"]] == [-1]
