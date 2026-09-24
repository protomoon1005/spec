"""시장분석 라벨 정의 테스트. 픽스처도 ML 의존성도 요구하지 않는다."""
from __future__ import annotations

import random
from dataclasses import replace
from datetime import date

import pytest

from app.views.market import labels
from app.views.market.features import synthetic_series
from app.views.market.labels import HORIZON, make_label, realized_outcome

START = date(2023, 1, 2)


def _stepped_walk(days: int, seed: int = 11):
    # 걸음을 -1/0/+1 로 두면 20일 누적 수익률이 정확히 0 인 구간이 자주 나온다.
    # 사건 경계(r = 0)에서 두 함수가 같은 쪽으로 떨어지는지 보려고 일부러 만든다.
    rng = random.Random(seed)
    closes = [100.0]
    for _ in range(days - 1):
        closes.append(closes[-1] + rng.choice((-1.0, 0.0, 1.0)))
    return synthetic_series(START, closes)


@pytest.mark.parametrize("theta", [0.0, 0.02])
def test_label_and_outcome_share_one_event_definition(monkeypatch, theta: float) -> None:
    monkeypatch.setattr(labels, "THETA", theta)
    bars = _stepped_walk(300)
    index_of = {bar.trade_date: i for i, bar in enumerate(bars)}

    labelled = make_label(bars, train_end=bars[-1].trade_date)

    assert labelled
    for trade_date, label in labelled:
        i = index_of[trade_date]
        assert label == realized_outcome(bars[i].close, bars[i + HORIZON].close), trade_date

    # 경계 r = 0 이 표본 안에 실제로 있었고 (θ = 0 일 때) 사건이 아닌 쪽으로 갔다.
    flat = [
        d for d, _ in labelled if bars[index_of[d] + HORIZON].close == bars[index_of[d]].close
    ]
    if theta == 0.0:
        assert flat
        assert len(labelled) == len(bars) - HORIZON
    assert all(label == 0 for d, label in labelled if d in flat)
    assert (realized_outcome(100.0, 101.0), realized_outcome(100.0, 100.0)) == (1, 0)
    assert realized_outcome(100.0, 99.0) == 0


def test_realized_outcome_rejects_non_positive_base_close() -> None:
    for close_t in (0.0, -1.0):
        with pytest.raises(ValueError):
            realized_outcome(close_t, 100.0)


def test_rows_whose_label_resolves_after_train_end_are_excluded() -> None:
    bars = _stepped_walk(120)
    train_end = bars[60].trade_date

    labelled = make_label(bars, train_end=train_end)
    dates = [d for d, _ in labelled]

    # t+20 == train_end 인 행(40)까지 들어가고, 41 부터는 빠진다.
    assert dates[-1] == bars[60 - HORIZON].trade_date
    assert bars[61 - HORIZON].trade_date not in dates

    # train_end 이후 가격을 바꿔도 학습 표본이 그대로다 — 미래가 새지 않는다.
    shocked = bars[:61] + [replace(bar, close=bar.close * 3) for bar in bars[61:]]
    assert make_label(shocked, train_end=train_end) == labelled
