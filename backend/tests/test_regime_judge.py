"""FN-406 국면 판정 테스트. 픽스처도 ML 의존성도 없이 돈다."""
from __future__ import annotations

import pytest

from app.views.regime.judge import (
    LABEL_RISK_OFF,
    LABEL_RISK_ON,
    LABEL_UNKNOWN,
    MIN_AVAILABLE_INDICATORS,
    THRESHOLD_SET_VERSION,
    THRESHOLDS,
    judge,
)

CALM = {"VIX_CLOSE": 14.0, "CREDIT_SPREAD_BAA10Y": 1.55, "USDKRW_GAP60": -0.01, "TREND_GAP": 0.03}
PANIC = {"VIX_CLOSE": 30.0, "CREDIT_SPREAD_BAA10Y": 2.30, "USDKRW_GAP60": 0.06, "TREND_GAP": -0.08}


def test_calm_market_is_risk_on_and_panic_is_risk_off() -> None:
    calm = judge(CALM)
    panic = judge(PANIC)

    assert calm.label == LABEL_RISK_ON
    assert panic.label == LABEL_RISK_OFF
    assert calm.intensity > 0 > panic.intensity


@pytest.mark.parametrize("rule", THRESHOLDS, ids=[rule.key for rule in THRESHOLDS])
def test_label_flips_across_each_threshold(rule) -> None:
    # 한 지표만 움직여 경계 양쪽을 본다. 나머지는 정확히 임계값에 둬서 기여도 0.
    neutral = {other.key: other.threshold for other in THRESHOLDS}

    step = rule.scale * 0.5
    risk_off_side = dict(neutral, **{rule.key: rule.threshold + step})
    risk_on_side = dict(neutral, **{rule.key: rule.threshold - step})
    if rule.direction == "below_is_risk_off":
        risk_off_side, risk_on_side = risk_on_side, risk_off_side

    assert judge(risk_off_side).label == LABEL_RISK_OFF
    assert judge(risk_on_side).label == LABEL_RISK_ON


@pytest.mark.parametrize(
    "indicators",
    [CALM, PANIC, {key: 0.0 for key in (rule.key for rule in THRESHOLDS)},
     {"VIX_CLOSE": 1e6, "CREDIT_SPREAD_BAA10Y": -1e6, "USDKRW_GAP60": 1e6, "TREND_GAP": -1e6}],
)
def test_intensity_stays_in_range(indicators: dict) -> None:
    result = judge(indicators)
    assert -1.0 <= result.intensity <= 1.0


def test_threshold_state_reports_every_indicator() -> None:
    state = judge(PANIC).threshold_state

    assert state["threshold_set_version"] == THRESHOLD_SET_VERSION
    assert state["available_count"] == len(THRESHOLDS)
    assert set(state["indicators"]) == {rule.key for rule in THRESHOLDS}

    for rule in THRESHOLDS:
        entry = state["indicators"][rule.key]
        # P06 온도탭이 "지표별 현재값과 임계값 대비 위치"를 그려야 한다.
        assert entry["value"] == pytest.approx(PANIC[rule.key])
        assert entry["threshold"] == rule.threshold
        assert entry["breached"] is True
        assert entry["missing"] is False
        assert -1.0 <= entry["score"] <= 1.0


def test_missing_indicator_is_reported_not_raised() -> None:
    # 지표 하나가 비는 것은 판단 계층의 정상 상태다. 남은 지표로 판정한다.
    partial = dict(PANIC)
    partial["TREND_GAP"] = None

    result = judge(partial)

    assert result.label == LABEL_RISK_OFF
    assert result.threshold_state["available_count"] == len(THRESHOLDS) - 1
    assert result.threshold_state["indicators"]["TREND_GAP"]["missing"] is True
    assert result.threshold_state["indicators"]["TREND_GAP"]["value"] is None


def test_absent_key_is_treated_as_missing() -> None:
    result = judge({"VIX_CLOSE": 30.0, "CREDIT_SPREAD_BAA10Y": 2.3})
    assert result.threshold_state["indicators"]["USDKRW_GAP60"]["missing"] is True
    assert result.label == LABEL_RISK_OFF


def test_nan_is_treated_as_missing() -> None:
    result = judge(dict(PANIC, VIX_CLOSE=float("nan")))
    assert result.threshold_state["indicators"]["VIX_CLOSE"]["missing"] is True


def test_too_few_indicators_is_unknown_not_a_guess() -> None:
    # 근거가 하나뿐인데 "위험회피 국면"이라고 말하면 판정이 아니라 추측이다.
    result = judge({"VIX_CLOSE": 40.0})

    assert result.label == LABEL_UNKNOWN
    assert result.intensity == 0.0
    assert result.threshold_state["available_count"] == 1
    assert result.threshold_state["min_required"] == MIN_AVAILABLE_INDICATORS
    assert "reason" in result.threshold_state


def test_all_missing_is_unknown() -> None:
    assert judge({}).label == LABEL_UNKNOWN


def test_same_input_twice_is_identical() -> None:
    assert judge(PANIC) == judge(PANIC)


def test_threshold_set_has_a_version_for_reproducibility() -> None:
    # 임계값이 바뀌면 과거 판정을 재현할 수 없다. 버전이 스냅샷에 남아야 한다.
    assert THRESHOLD_SET_VERSION.startswith("regime-v")
    assert judge(CALM).threshold_state["threshold_set_version"] == THRESHOLD_SET_VERSION
