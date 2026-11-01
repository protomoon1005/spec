"""FN-408 Hedge 가중치 갱신 회귀 테스트.

test_integrator.py 와 같이 픽스처를 하나도 요청하지 않는다 — postgres 도 ML
의존성도 없이 돈다. 확정 수치(η 0.5 · floor 0.10 · 윈도우 60)와 2026-09-12에
확정된 해석(롤링 재계산 · water-filling)을 CI가 매 PR마다 지켜주게 하는 것이
이 파일의 목적이다.
"""
from __future__ import annotations

import math

import pytest

from app.contracts.view_weights import WEIGHT_FLOOR
from app.views.hedge import (
    ETA,
    UPDATE_RULE_VERSION,
    WINDOW_TRADING_DAYS,
    apply_floor,
    brier_loss,
    rolling_weights,
)

VIEWS = ("market", "sentiment", "regime")

# 합성 손실: 시장분석은 계속 맞고(p=0.8, y=1), 감성은 계속 빗나가고(p=0.2, y=1),
# 온도는 동전 던지기(p=0.5) 다.
GOOD_LOSS = brier_loss(0.8, 1)  # 0.04
BAD_LOSS = brier_loss(0.2, 1)  # 0.64
COIN_LOSS = brier_loss(0.5, 1)  # 0.25


def _history(days: int) -> dict[str, list[float]]:
    return {
        "market": [GOOD_LOSS] * days,
        "sentiment": [BAD_LOSS] * days,
        "regime": [COIN_LOSS] * days,
    }


def _naive_clip_then_renormalize(weights: dict[str, float], floor: float) -> dict[str, float]:
    # 상세설계서 1.5.5 의사코드를 글자 그대로 옮긴 것 — "클립 후 재정규화".
    clipped = {name: max(value, floor) for name, value in weights.items()}
    total = math.fsum(clipped.values())
    return {name: value / total for name, value in clipped.items()}


def test_confirmed_constants() -> None:
    assert ETA == 0.5
    assert WEIGHT_FLOOR == 0.10
    assert WINDOW_TRADING_DAYS == 60
    assert UPDATE_RULE_VERSION == "hedge-rolling-v0.1-eta0.5-floor0.10-w60"


def test_brier_loss() -> None:
    assert brier_loss(1.0, 1) == 0.0
    assert brier_loss(0.0, 1) == 1.0
    assert brier_loss(0.5, 0) == 0.25
    assert brier_loss(0.8, 1) == pytest.approx(0.04)


@pytest.mark.parametrize(("prob", "outcome"), [(1.5, 1), (-0.1, 0), (0.5, 2)])
def test_brier_loss_rejects_bad_input(prob: float, outcome: int) -> None:
    with pytest.raises(ValueError):
        brier_loss(prob, outcome)


def test_water_filling_regression() -> None:
    # 확정 반례. (0.95, 0.03, 0.02) -> (0.80, 0.10, 0.10)
    result = apply_floor({"a": 0.95, "b": 0.03, "c": 0.02})
    assert result["a"] == pytest.approx(0.80, abs=1e-12)
    assert result["b"] == pytest.approx(0.10, abs=1e-12)
    assert result["c"] == pytest.approx(0.10, abs=1e-12)
    assert math.fsum(result.values()) == pytest.approx(1.0, abs=1e-9)


def test_naive_clip_then_renormalize_breaks_the_floor() -> None:
    # 이 테스트는 "왜 water-filling 이어야 하는가"를 코드로 고정한다.
    # 문서 1.5.5 의사코드대로 하면 하한이 지켜지지 않는다는 걸 직접 보여준다.
    weights = {"a": 0.95, "b": 0.03, "c": 0.02}
    naive = _naive_clip_then_renormalize(weights, WEIGHT_FLOOR)

    assert naive["b"] == pytest.approx(0.086956, abs=1e-5)
    assert naive["b"] < WEIGHT_FLOOR  # 클립했는데도 다시 하한 밑이다
    assert naive["c"] < WEIGHT_FLOOR

    correct = apply_floor(weights)
    assert min(correct.values()) >= WEIGHT_FLOOR - 1e-12


def test_floor_is_never_violated_over_the_whole_window() -> None:
    for days in range(0, WINDOW_TRADING_DAYS + 1):
        weights = rolling_weights(_history(days))
        assert min(weights.values()) >= WEIGHT_FLOOR - 1e-12, f"{days}일차에 하한이 깨졌다"
        assert math.fsum(weights.values()) == pytest.approx(1.0, abs=1e-9)


def test_convergence_over_60_days() -> None:
    initial = rolling_weights(_history(0))
    after_20 = rolling_weights(_history(20))
    after_60 = rolling_weights(_history(60))

    # 초기: 손실 이력이 비면 별도 분기 없이 1/3 균등이 나온다 (FN-409).
    for view in VIEWS:
        assert initial[view] == pytest.approx(1 / 3, abs=1e-12)

    # 방향: 계속 맞는 관점은 올라가고 계속 틀리는 관점은 내려간다.
    assert after_20["market"] > initial["market"]
    assert after_20["sentiment"] < initial["sentiment"]
    assert after_60["market"] > initial["market"]
    assert after_60["sentiment"] < initial["sentiment"]

    # 벌어진다: 최고와 최저의 격차가 0에서 크게 벌어진다.
    assert after_60["market"] - after_60["sentiment"] > 0.5

    # 하한은 끝까지 지켜진다. 이 합성 입력은 20일차에 이미 하한에 닿아 포화한다
    # (문서 1.5.5 표는 예시지 규격이 아니므로 수치를 맞추려 하지 않는다).
    assert after_60["sentiment"] == pytest.approx(WEIGHT_FLOOR, abs=1e-12)
    assert after_60["market"] == pytest.approx(0.80, abs=1e-12)


def test_ordering_before_the_floor_saturates() -> None:
    # 손실 차이가 덜 극단적이면 세 관점이 하한에 닿기 전 순서대로 벌어진다.
    mild = {
        "market": [brier_loss(0.7, 1)] * 20,  # 0.09
        "sentiment": [brier_loss(0.6, 1)] * 20,  # 0.16
        "regime": [COIN_LOSS] * 20,  # 0.25
    }
    weights = rolling_weights(mild)

    assert weights["market"] > weights["sentiment"] > weights["regime"]
    assert min(weights.values()) > WEIGHT_FLOOR  # 아무도 하한에 닿지 않았다
    assert weights["market"] == pytest.approx(0.588761, abs=1e-6)


def test_empty_history_is_uniform() -> None:
    weights = rolling_weights({view: [] for view in VIEWS})
    for view in VIEWS:
        assert weights[view] == pytest.approx(1 / 3, abs=1e-12)


def test_only_the_last_window_matters() -> None:
    # 롤링 재계산이므로 윈도우 밖의 과거는 결과에 영향을 주지 않는다.
    recent = _history(WINDOW_TRADING_DAYS)
    with_ancient_past = {view: [1.0 - recent[view][0]] * 100 + recent[view] for view in VIEWS}
    assert rolling_weights(with_ancient_past) == rolling_weights(recent)


def test_extreme_losses_do_not_underflow() -> None:
    # S_k 가 윈도우 상한(60)에 닿는 극단. min S 를 빼지 않으면 exp(-30) 까지
    # 내려가 유효숫자를 잃는 자리다. NaN 도 0 나눗셈도 나면 안 된다.
    extreme = {"market": [0.0] * 60, "sentiment": [1.0] * 60, "regime": [0.0] * 60}
    weights = rolling_weights(extreme)

    assert all(math.isfinite(value) for value in weights.values())
    assert math.fsum(weights.values()) == pytest.approx(1.0, abs=1e-9)
    assert weights["sentiment"] == pytest.approx(WEIGHT_FLOOR, abs=1e-12)
    assert weights["market"] == pytest.approx(0.45, abs=1e-12)


def test_identical_history_twice_is_identical_output() -> None:
    first = rolling_weights(_history(37))
    second = rolling_weights(_history(37))
    assert first == second
    assert list(first) == list(second)


def test_unequal_history_lengths_raise() -> None:
    uneven = {"market": [0.1] * 10, "sentiment": [0.1] * 9, "regime": [0.1] * 10}
    with pytest.raises(ValueError, match="길이가 다르다"):
        rolling_weights(uneven)


@pytest.mark.parametrize("bad", [1.5, -0.1])
def test_loss_out_of_range_raises(bad: float) -> None:
    history = _history(5)
    history["market"][2] = bad
    with pytest.raises(ValueError, match=r"\[0,1\]"):
        rolling_weights(history)


def test_empty_views_raise() -> None:
    with pytest.raises(ValueError, match="관점이 하나도 없다"):
        rolling_weights({})


def test_floor_infeasible_raises() -> None:
    # 관점 11개 × 하한 0.10 = 1.1 > 1 이라 만족할 수 없다.
    weights = {f"v{i}": 1.0 for i in range(11)}
    with pytest.raises(ValueError, match="만족할 수 없다"):
        apply_floor(weights)


def test_floor_exactly_saturating_is_uniform() -> None:
    # floor × n == 1 인 경계. 전부 하한이라 균등이 된다.
    result = apply_floor({"a": 0.9, "b": 0.05, "c": 0.05}, floor=1 / 3)
    for view in ("a", "b", "c"):
        assert result[view] == pytest.approx(1 / 3, abs=1e-12)


def test_apply_floor_rejects_negative_weight() -> None:
    with pytest.raises(ValueError, match="음수 가중치"):
        apply_floor({"a": 1.2, "b": -0.2})
