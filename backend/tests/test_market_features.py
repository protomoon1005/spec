"""피처 계산 회귀 테스트 (T2의 핵심 산출물).

**픽스처도 ML 의존성도 요구하지 않는다.** 미래 참조 금지는 이 프로젝트에서 가장
중요한 성질 중 하나라, 매 PR 에서 검증되도록 계산 코어를 stdlib 로 짰고 이 파일도
pandas 없이 돈다. pandas DataFrame 어댑터만 requires_ml 로 따로 뺀다.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from app.views.market.features import (
    CURRENT_FEATURE_SET_VERSION,
    FEATURE_SETS,
    RECOMMENDED_WARMUP_ROWS,
    PriceBar,
    compute_features,
    feature_names,
    synthetic_series,
)

START = date(2023, 1, 2)


def _flat(days: int, close: float = 100.0) -> list[PriceBar]:
    return synthetic_series(START, [close] * days)


def _drifting(days: int, start_close: float = 100.0, step: float = 1.0) -> list[PriceBar]:
    return synthetic_series(START, [start_close + step * i for i in range(days)])


def test_returns_every_feature_of_the_version() -> None:
    result = compute_features(_flat(80), as_of=START + timedelta(days=79))
    assert tuple(result) == FEATURE_SETS[CURRENT_FEATURE_SET_VERSION]
    assert len(result) == 9


def test_unknown_version_raises() -> None:
    with pytest.raises(ValueError, match="알 수 없는 피처셋 버전"):
        feature_names("v9.9-nope")


def test_flat_series_has_hand_checkable_values() -> None:
    bars = _flat(40)
    result = compute_features(bars, as_of=bars[-1].trade_date)

    assert result["ret_1"] == 0.0
    assert result["ret_5"] == 0.0
    assert result["ret_20"] == 0.0
    assert result["ma_gap_20"] == 0.0
    assert result["ma_gap_60"] is None  # 40행뿐이라 60일 이동평균은 결측
    assert result["vol_20"] == 0.0
    assert result["volume_ratio_20"] == 1.0
    assert result["rsi_14"] == 50.0  # 완전 평탄은 방향이 없다
    # 합성 봉의 고가/저가가 종가의 ±1% 라 TR 은 항상 종가의 2%, ATR 도 2%.
    assert result["atr_14_pct"] == pytest.approx(0.02, abs=1e-9)


def test_jump_on_the_last_bar() -> None:
    bars = synthetic_series(START, [100.0] * 19 + [110.0])
    result = compute_features(bars, as_of=bars[-1].trade_date)

    assert result["ret_1"] == pytest.approx(0.1, abs=1e-9)
    # SMA20 = (19*100 + 110) / 20 = 100.5
    assert result["ma_gap_20"] == pytest.approx(110 / 100.5 - 1, abs=1e-6)
    assert result["ret_5"] == pytest.approx(0.1, abs=1e-9)
    assert result["rsi_14"] > 50.0


def test_monotonic_rise_saturates_rsi() -> None:
    result = compute_features(_drifting(40), as_of=START + timedelta(days=39))
    assert result["rsi_14"] == 100.0


def test_short_history_is_missing_not_zero() -> None:
    # 0으로 채우면 "지표가 0"과 "이력이 모자람"을 모델이 구분하지 못한다.
    bars = _flat(3)
    result = compute_features(bars, as_of=bars[-1].trade_date)

    assert result["ret_1"] == 0.0  # 2행이면 계산된다
    for name in ("ret_5", "ret_20", "ma_gap_20", "ma_gap_60", "rsi_14", "atr_14_pct", "vol_20"):
        assert result[name] is None, name


def test_empty_history_is_all_missing() -> None:
    bars = _flat(30)
    before_everything = START - timedelta(days=1)
    result = compute_features(bars, as_of=before_everything)
    assert all(value is None for value in result.values())


# ── 미래 참조 금지 ───────────────────────────────────────────────────


def test_appending_future_bars_does_not_change_features() -> None:
    base = _drifting(60)
    as_of = base[-1].trade_date
    future = synthetic_series(as_of + timedelta(days=1), [999.0, 1.0, 500.0])

    assert compute_features(base + future, as_of=as_of) == compute_features(base, as_of=as_of)


def test_mutating_future_bars_does_not_change_features() -> None:
    base = _drifting(60)
    as_of = base[-1].trade_date
    one_future = synthetic_series(as_of + timedelta(days=1), [120.0])
    another_future = synthetic_series(as_of + timedelta(days=1), [-0.0 + 5.0])

    assert compute_features(base + one_future, as_of=as_of) == compute_features(
        base + another_future, as_of=as_of
    )


def test_as_of_in_the_middle_matches_a_truncated_series() -> None:
    # rolling 윈도우가 뒤를 보면 여기서 깨진다.
    full = _drifting(90)
    as_of = full[44].trade_date

    assert compute_features(full, as_of=as_of) == compute_features(full[:45], as_of=as_of)


def test_every_as_of_in_the_series_is_leak_free() -> None:
    full = _drifting(70)
    for cut in range(1, len(full)):
        as_of = full[cut].trade_date
        assert compute_features(full, as_of=as_of) == compute_features(
            full[: cut + 1], as_of=as_of
        ), f"{as_of} 에서 미래를 봤다"


# ── 결정론과 입력 형태 ───────────────────────────────────────────────


def test_same_input_twice_is_identical() -> None:
    bars = _drifting(40)
    as_of = bars[-1].trade_date
    assert compute_features(bars, as_of=as_of) == compute_features(bars, as_of=as_of)


def test_input_order_does_not_matter() -> None:
    bars = _drifting(40)
    as_of = bars[-1].trade_date
    shuffled = bars[20:] + bars[:20]
    assert compute_features(shuffled, as_of=as_of) == compute_features(bars, as_of=as_of)


def test_mapping_rows_are_accepted() -> None:
    bars = _drifting(30)
    as_of = bars[-1].trade_date
    rows = [
        {
            "trade_date": bar.trade_date,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
        }
        for bar in bars
    ]
    assert compute_features(rows, as_of=as_of) == compute_features(bars, as_of=as_of)


def test_duplicate_trade_date_raises() -> None:
    bars = _flat(5)
    with pytest.raises(ValueError, match="두 번 들어왔다"):
        compute_features(bars + [bars[-1]], as_of=bars[-1].trade_date)


def test_missing_column_raises() -> None:
    with pytest.raises(ValueError, match="필요한 항목이 없다"):
        compute_features([{"trade_date": START, "close": 100.0}], as_of=START)


def test_all_values_are_json_safe() -> None:
    # 적재 함수가 NaN/Inf 를 거부하므로 계산 쪽에서 그런 값이 나오면 안 된다.
    result = compute_features(_drifting(80), as_of=START + timedelta(days=79))
    for name, value in result.items():
        assert value is None or math.isfinite(value), name


@pytest.mark.requires_ml
def test_pandas_dataframe_adapter() -> None:
    # pandas 는 ml extra 로만 들어온다. 어댑터가 실제 DataFrame 을 받는지만
    # 여기서 확인하고, 계산 자체의 회귀는 위 테스트들이 의존성 없이 지킨다.
    import pandas as pd

    bars = _drifting(40)
    as_of = bars[-1].trade_date
    frame = pd.DataFrame(
        [
            {"open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume}
            for b in bars
        ],
        index=pd.to_datetime([b.trade_date for b in bars]),
    )

    assert compute_features(frame, as_of=as_of) == compute_features(bars, as_of=as_of)


@pytest.mark.requires_ml
def test_pandas_dataframe_with_trade_date_column() -> None:
    import pandas as pd

    bars = _drifting(40)
    as_of = bars[-1].trade_date
    frame = pd.DataFrame(
        [
            {
                "trade_date": b.trade_date,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
            }
            for b in bars
        ]
    )

    assert compute_features(frame, as_of=as_of) == compute_features(bars, as_of=as_of)


# ── Wilder 계열의 무한 기억 ──────────────────────────────────────────


def _random_walk(days: int, seed: int = 7) -> list[PriceBar]:
    import random

    rng = random.Random(seed)
    closes = [100.0]
    for _ in range(days - 1):
        closes.append(round(closes[-1] * (1 + rng.uniform(-0.02, 0.02)), 4))
    return synthetic_series(START, closes)


def test_window_features_do_not_depend_on_history_length() -> None:
    full = _random_walk(200)
    as_of = full[-1].trade_date
    reference = compute_features(full, as_of=as_of)

    for cut in (30, 100, 170):
        trimmed = compute_features(full[cut:], as_of=as_of)
        for name in ("ret_1", "ret_5", "ret_20", "ma_gap_20", "vol_20", "volume_ratio_20"):
            assert trimmed[name] == reference[name], name


def test_wilder_features_converge_after_the_recommended_warmup() -> None:
    # rsi_14 · atr_14_pct 는 seed 이후 매 행을 지수적으로 섞어 기억이 무한하다.
    # 이력이 짧으면 값이 달라지므로, 재현하려면 as_of 뿐 아니라 입력 구간의
    # 시작일도 고정해야 한다. 권장 워밍업을 넘기면 실용적으로 일치한다.
    full = _random_walk(300)
    as_of = full[-1].trade_date
    reference = compute_features(full, as_of=as_of)

    enough = compute_features(full[-RECOMMENDED_WARMUP_ROWS:], as_of=as_of)
    assert enough["rsi_14"] == pytest.approx(reference["rsi_14"], abs=0.01)
    assert enough["atr_14_pct"] == pytest.approx(reference["atr_14_pct"], abs=1e-5)


def test_too_little_history_visibly_changes_wilder_features() -> None:
    # 위 성질의 반대쪽 — "짧아도 괜찮다"고 오해하지 않도록 차이를 고정해 둔다.
    full = _random_walk(200)
    as_of = full[-1].trade_date
    reference = compute_features(full, as_of=as_of)
    barely = compute_features(full[-31:], as_of=as_of)

    assert abs(barely["rsi_14"] - reference["rsi_14"]) > 1.0
