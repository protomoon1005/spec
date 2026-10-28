"""BacktestJudge.preview_rebalance / commit — 신호형 리밸런싱(RB_SIGNAL) 판정용.

판정용 판단은 관점 가중치 상태를 바꾸지 않고, commit(preview_rebalance(d)) 는
record_outcome(d) 뒤 judge(d) 와 같은 신호 · 같은 상태를 내야 한다. 그래야 신호형도
"채점 · 갱신은 리밸런싱일에만" 이라는 달력형 규칙을 따른다. DB 없이 목업 스코어러로 돈다.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.views import bridge
from app.views.bridge import MOCK_SCORERS, BacktestJudge

START = date(2024, 1, 1)
TICKERS = ["069500", "133690"]


def _prices(count: int = 400) -> dict:
    def closes(seed: float, step: float) -> list[float]:
        out, value = [], seed
        for i in range(count):
            value += step * (1 if i % 3 else -1.5)
            out.append(round(value, 4))
        return out

    series = {TICKERS[0]: closes(100.0, 0.7), TICKERS[1]: closes(250.0, 1.3)}
    return {t: [(START + timedelta(days=i), c) for i, c in enumerate(v)] for t, v in series.items()}


@pytest.fixture(autouse=True)
def mock_registry(monkeypatch):
    monkeypatch.setattr(bridge, "SCORERS", MOCK_SCORERS)


def _state(judge: BacktestJudge):
    return judge.loss_history(), judge._pending, dict(judge.last_weights)


DAYS = [START + timedelta(days=d) for d in (200, 230, 260, 290, 320)]


def test_commit_of_preview_equals_record_outcome_then_judge():
    prices = _prices()
    a, b = BacktestJudge(TICKERS), BacktestJudge(TICKERS)
    for day in DAYS:
        a.record_outcome(prices, as_of=day)
        sa = a.judge(prices, as_of=day)
        sb = b.commit(b.preview_rebalance(prices, as_of=day))
        assert sa == sb
        assert _state(a) == _state(b)
    assert len(a.loss_history()["market"]) == len(DAYS) - 1  # 채점이 실제로 일어났다


def test_preview_does_not_touch_the_weight_state():
    prices = _prices()
    judge = BacktestJudge(TICKERS)
    judge.commit(judge.preview_rebalance(prices, as_of=DAYS[0]))
    before = _state(judge)

    for day in DAYS[1:]:
        judge.preview_rebalance(prices, as_of=day)  # 판정만 하고 리밸런싱하지 않은 날들

    assert _state(judge) == before
    # 다음 리밸런싱일의 채점은 직전 "리밸런싱일" 판단을 기준으로 한다
    later = judge.preview_rebalance(prices, as_of=DAYS[-1])
    assert later["settles"] and judge._pending[0] == DAYS[0]


def test_stale_preview_is_refused():
    prices = _prices()
    judge = BacktestJudge(TICKERS)
    first = judge.preview_rebalance(prices, as_of=DAYS[0])
    judge.commit(judge.preview_rebalance(prices, as_of=DAYS[1]))

    with pytest.raises(RuntimeError):
        judge.commit(first)
