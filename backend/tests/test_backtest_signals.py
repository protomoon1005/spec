"""조건형 백테스트 지표 시계열(app/backtest/signals.py) 테스트. 룩어헤드가 없는지 본다."""
from __future__ import annotations

import math

import pytest

from app.backtest import features, signals

N = 160
CLOSES = [100 + 10 * math.sin(i / 7) + i * 0.05 + (i % 5) * 0.3 for i in range(N)]
VOLUMES = [1_000 + 200 * math.cos(i / 3) + (i % 11) * 17 for i in range(N)]
CASES = [
    ("close", None), ("ma", 5), ("ma", 120), ("rsi", 14), ("ret", 1), ("ret", 60),
    ("vol", 20), ("volume_ratio", 20),
]


@pytest.mark.parametrize(("indicator", "window"), CASES)
def test_full_series_equals_truncated_series(indicator, window):
    # 한 번에 계산한 k번째 값 = k번째 행까지만 주고 계산한 마지막 값. 앞으로 나올 행을 보지 않는다
    full = signals.indicator_series(CLOSES, VOLUMES, indicator, window)
    for k in range(0, N, 7):
        cut = signals.indicator_series(CLOSES[: k + 1], VOLUMES[: k + 1], indicator, window)
        assert cut[-1] == full[k]


@pytest.mark.parametrize(("indicator", "window"), CASES)
def test_changing_the_future_does_not_change_the_past(indicator, window):
    full = signals.indicator_series(CLOSES, VOLUMES, indicator, window)
    future_changed = CLOSES[:100] + [c * 3 for c in CLOSES[100:]]
    other = signals.indicator_series(future_changed, VOLUMES[:100] + [v * 9 for v in VOLUMES[100:]],
                                     indicator, window)
    assert other[:100] == full[:100]


def test_same_definition_as_features():
    rsi = signals.indicator_series(CLOSES, VOLUMES, "rsi", 14)
    ret = signals.indicator_series(CLOSES, VOLUMES, "ret", 20)
    vol = signals.indicator_series(CLOSES, VOLUMES, "vol", 20)
    for k in range(N):
        head = CLOSES[: k + 1]
        expected_rsi = features._wilder_rsi(head, 14)
        if expected_rsi is None:
            assert rsi[k] is None
        else:
            assert rsi[k] == pytest.approx(expected_rsi)
        assert ret[k] == features._return_over(head, 20)
        assert vol[k] == features._return_stdev(head, 20)


def test_warmup_is_none_not_zero():
    ma = signals.indicator_series(CLOSES, VOLUMES, "ma", 120)
    assert ma[118] is None
    assert ma[119] == pytest.approx(sum(CLOSES[:120]) / 120)
    rsi = signals.indicator_series(CLOSES, VOLUMES, "rsi", 14)
    assert rsi[13] is None and rsi[14] is not None


def test_ret_is_a_fraction():
    closes = [100.0] * 5 + [110.0]
    assert signals.indicator_series(closes, [1.0] * 6, "ret", 5)[5] == pytest.approx(0.10)


def test_volume_ratio_compares_today_with_the_past_20_days():
    volumes = [100.0] * 20 + [300.0]
    ratio = signals.indicator_series([1.0] * 21, volumes, "volume_ratio", 20)
    assert ratio[19] is None
    assert ratio[20] == pytest.approx(3.0)


def test_rejects_non_price_indicator_and_missing_window():
    with pytest.raises(ValueError):
        signals.indicator_series(CLOSES, VOLUMES, "entry_return", None)
    with pytest.raises(ValueError):
        signals.indicator_series(CLOSES, VOLUMES, "ma", None)
