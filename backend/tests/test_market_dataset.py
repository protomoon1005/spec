"""시장분석 학습 표본(app/views/market/dataset.py). DB·ML 없이 합성 시계열로 돈다."""
from __future__ import annotations

import random
from dataclasses import replace
from datetime import date, timedelta

from app.views.market.dataset import build_samples
from app.views.market.features import FEATURE_WARMUP_ROWS, compute_features, synthetic_series
from app.views.market.labels import HORIZON, make_label

START = date(2020, 1, 1)


def _bars(n: int = 300, seed: int = 7):
    rng = random.Random(seed)
    closes, price = [], 100.0
    for _ in range(n):
        price *= 1.0 + rng.uniform(-0.02, 0.02)
        closes.append(price)
    return synthetic_series(START, closes)


def test_label_date_never_after_cutoff():
    bars = _bars()
    cutoff = bars[250].trade_date
    samples = build_samples(bars, cutoff=cutoff)
    assert samples
    assert all(sample.label_date <= cutoff for sample in samples)
    assert all(sample.trade_date < sample.label_date for sample in samples)


def test_label_date_is_horizon_rows_ahead_and_labels_match_definition():
    bars = _bars()
    cutoff = bars[-1].trade_date
    samples = build_samples(bars, cutoff=cutoff)
    index = {bar.trade_date: i for i, bar in enumerate(bars)}
    labels = dict(make_label(bars, train_end=cutoff))
    for sample in samples:
        assert index[sample.label_date] - index[sample.trade_date] == HORIZON
        assert sample.label == labels[sample.trade_date]


def test_warmup_rows_are_required():
    bars = _bars()
    samples = build_samples(bars, cutoff=bars[-1].trade_date)
    assert samples[0].trade_date == bars[FEATURE_WARMUP_ROWS - 1].trade_date


def test_features_use_trailing_warmup_window_like_bridge():
    bars = _bars()
    samples = build_samples(bars, cutoff=bars[-1].trade_date)
    sample = samples[37]
    i = next(k for k, bar in enumerate(bars) if bar.trade_date == sample.trade_date)
    expected = compute_features(bars[i + 1 - FEATURE_WARMUP_ROWS : i + 1], as_of=sample.trade_date)
    assert sample.features == expected


def test_future_rows_do_not_change_samples():
    # cutoff 뒤 행을 마음대로 바꿔도 표본(피처·라벨)이 그대로여야 한다.
    bars = _bars()
    cutoff = bars[220].trade_date
    tampered = bars[:221] + [replace(bar, close=bar.close * 3, high=bar.high * 3) for bar in bars[221:]]
    assert build_samples(bars, cutoff=cutoff) == build_samples(tampered, cutoff=cutoff)
    assert build_samples(bars[:221], cutoff=cutoff) == build_samples(bars, cutoff=cutoff)


def test_memo_is_filled_and_reused():
    bars = _bars()
    memo: dict = {}
    first = build_samples(bars, cutoff=bars[200].trade_date, memo=memo)
    assert set(memo) == {sample.trade_date for sample in first}
    # 메모의 값이 쓰이는지 — 일부러 바꿔 넣으면 그 값이 나온다.
    marker = {"ret_1": 123.0}
    memo[first[0].trade_date] = marker
    again = build_samples(bars, cutoff=bars[260].trade_date, memo=memo)
    assert again[0].features is marker
    assert len(again) > len(first)


def test_empty_when_history_too_short():
    bars = _bars(n=FEATURE_WARMUP_ROWS + HORIZON - 1)
    assert build_samples(bars, cutoff=bars[-1].trade_date + timedelta(days=1)) == []
