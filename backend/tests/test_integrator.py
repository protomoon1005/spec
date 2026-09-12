"""FN-407 신호 통합 회귀 테스트.

픽스처를 하나도 요청하지 않는 파일이다 — postgres 도 ML 의존성도 없이 돈다.
backend/tests/conftest.py 에 autouse 픽스처가 없고 engine 은 요청해야 만들어지므로,
이 파일은 DB가 꺼져 있어도 통과해야 한다. 그게 이 테스트의 존재 이유다:
검산표와 재현성은 CI가 매 PR마다 지켜줘야 하는 성질이다.
"""
from __future__ import annotations

import math
from datetime import date

import pytest

from app.contracts.integrated_signal import IntegratedSignal
from app.contracts.view_score import ViewScore
from app.views.integrator import DEADZONE, TANH_SCALE, integrate

AS_OF = date(2026, 9, 11)
TICKER = "069500"

# 상세설계서 1.5.4 검산표 (2026-09-08 확정본). 이 표가 기준 케이스다.
CHECK_TABLE: list[tuple[float, float]] = [
    (-1.00, -0.96),
    (-0.50, -0.76),
    (-0.25, -0.46),
    (-0.05, 0.0),
    (0.05, 0.0),
    (0.25, 0.46),
    (0.50, 0.76),
    (1.00, 0.96),
]

# 데드존에 걸려 0이 되는 칸. 근사 비교가 아니라 정확히 0.0 이어야 한다.
DEADZONE_CELLS = [-0.05, 0.05]


def _score(view_type: str, ticker: str, raw: float) -> ViewScore:
    # calibrated_prob 는 통합에 쓰이지 않는다(Hedge가 Brier score로 쓴다).
    # 형식을 맞추기 위해 raw 를 [0,1] 로 옮겨 채운다.
    return ViewScore(
        view_type=view_type,
        ticker=ticker,
        raw_score=raw,
        calibrated_prob=(raw + 1.0) / 2.0,
    )


def _single_view(m: float) -> IntegratedSignal:
    # 가중치 1.0 짜리 관점 하나면 가중합이 그대로 m 이 된다.
    return integrate([_score("market", TICKER, m)], as_of=AS_OF, weights={"market": 1.0})


@pytest.mark.parametrize(("m", "expected"), CHECK_TABLE)
def test_check_table(m: float, expected: float) -> None:
    assert _single_view(m).signals[TICKER] == pytest.approx(expected, abs=5e-3)


@pytest.mark.parametrize("m", DEADZONE_CELLS)
def test_deadzone_cells_are_exactly_zero(m: float) -> None:
    result = _single_view(m)
    assert result.signals[TICKER] == 0.0
    assert result.deadzone_applied == [TICKER]


@pytest.mark.parametrize("m", [x for x, _ in CHECK_TABLE if x not in DEADZONE_CELLS])
def test_non_deadzone_cells_are_untouched(m: float) -> None:
    assert _single_view(m).deadzone_applied == []


def test_rounding_must_not_precede_deadzone() -> None:
    # 이 테스트는 연산 순서 자체를 고정한다. tanh(0.05/0.5) = 0.099668 이고
    # 2자리로 먼저 반올림하면 0.10 이 되어 |s| < 0.10 이 False 가 된다.
    # 즉 "반올림 -> 데드존" 순서로 구현하면 검산표 ±0.05 칸이 뒤집힌다.
    naive = round(math.tanh(0.05 / TANH_SCALE), 2)
    assert naive == 0.10  # 반올림을 먼저 하면 데드존을 빠져나간다
    assert _single_view(0.05).signals[TICKER] == 0.0  # 실제 구현은 눌러야 한다


def test_deadzone_boundary_just_below() -> None:
    # |s| = 0.10 이 되는 지점은 m = 0.5 * atanh(0.10) = 0.0501677 이다.
    result = _single_view(0.0501)  # s = 0.099866 -> 데드존
    assert result.signals[TICKER] == 0.0
    assert result.deadzone_applied == [TICKER]


def test_deadzone_boundary_just_above() -> None:
    result = _single_view(0.0503)  # s = 0.100262 -> 살아남는다
    assert result.signals[TICKER] == pytest.approx(0.100262, abs=1e-6)
    assert result.deadzone_applied == []
    assert abs(result.signals[TICKER]) >= DEADZONE


def test_weight_sum_not_one_raises() -> None:
    # 합이 1이 아닌 가중치는 조용히 정규화하지 않고 예외로 막는다.
    scores = [_score("market", TICKER, 0.5), _score("sentiment", TICKER, 0.5)]
    with pytest.raises(ValueError, match="가중치 합이 1이 아니다"):
        integrate(scores, as_of=AS_OF, weights={"market": 0.5, "sentiment": 0.4})


def test_float_noise_in_weight_sum_is_tolerated() -> None:
    # 1/3 세 개의 합은 부동소수 오차로 정확히 1.0 이 아니다. 그건 통과시킨다.
    third = 1.0 / 3.0
    scores = [_score(v, TICKER, 0.6) for v in ("market", "sentiment", "regime")]
    weights = {"market": third, "sentiment": third, "regime": third}
    assert integrate(scores, as_of=AS_OF, weights=weights).signals[TICKER] == pytest.approx(
        math.tanh(0.6 / TANH_SCALE), abs=1e-6
    )


def test_negative_weight_raises() -> None:
    scores = [_score("market", TICKER, 0.5), _score("sentiment", TICKER, 0.5)]
    with pytest.raises(ValueError, match="음수 가중치"):
        integrate(scores, as_of=AS_OF, weights={"market": 1.2, "sentiment": -0.2})


def test_two_views_only() -> None:
    # 관점이 둘만 들어온 경우. 두 가중치의 합이 1이면 그대로 돈다.
    scores = [_score("market", TICKER, 0.9), _score("regime", TICKER, 0.3)]
    result = integrate(scores, as_of=AS_OF, weights={"market": 0.5, "regime": 0.5})

    assert sorted(result.view_weights_used) == ["market", "regime"]
    assert result.signals[TICKER] == pytest.approx(math.tanh(0.6 / TANH_SCALE), abs=1e-6)


def test_neutral_view_dilutes_and_differs_from_two_view_run() -> None:
    # 위와 구분되는 경우: 관점은 셋 다 있는데 하나가 중립(raw 0.0)이다.
    # 중립 관점도 가중치를 그대로 소비하므로 가중합이 희석된다 — 관점이 아예
    # 빠진 2관점 실행과 결과가 달라야 한다.
    third = 1.0 / 3.0
    scores = [
        _score("market", TICKER, 0.9),
        _score("regime", TICKER, 0.3),
        _score("sentiment", TICKER, 0.0),  # signal_rules.sentiment 가 None -> 중립
    ]
    three = integrate(
        scores, as_of=AS_OF, weights={"market": third, "regime": third, "sentiment": third}
    )
    two = integrate(scores[:2], as_of=AS_OF, weights={"market": 0.5, "regime": 0.5})

    assert three.signals[TICKER] == pytest.approx(math.tanh(0.4 / TANH_SCALE), abs=1e-6)
    assert three.signals[TICKER] != two.signals[TICKER]
    assert three.per_view_scores["sentiment"][TICKER] == 0.0


def test_view_with_weight_but_no_scores_raises() -> None:
    # 중립 반환과 "관점이 통째로 빠진 것"은 다르다. 후자는 버그라 막는다.
    scores = [_score("market", TICKER, 0.9), _score("regime", TICKER, 0.3)]
    third = 1.0 / 3.0
    with pytest.raises(ValueError, match="점수가 하나도 없는 관점"):
        integrate(scores, as_of=AS_OF, weights={"market": third, "regime": third, "sentiment": third})


def test_score_without_weight_raises() -> None:
    scores = [_score("market", TICKER, 0.9), _score("regime", TICKER, 0.3)]
    with pytest.raises(ValueError, match="가중치가 없는 관점"):
        integrate(scores, as_of=AS_OF, weights={"market": 1.0})


def test_partial_ticker_coverage_raises() -> None:
    scores = [
        _score("market", "069500", 0.9),
        _score("market", "232080", 0.5),
        _score("regime", "069500", 0.3),
    ]
    with pytest.raises(ValueError, match="점수를 안 낸 종목"):
        integrate(scores, as_of=AS_OF, weights={"market": 0.5, "regime": 0.5})


def test_duplicate_score_raises() -> None:
    scores = [_score("market", TICKER, 0.9), _score("market", TICKER, 0.1)]
    with pytest.raises(ValueError, match="두 번 들어왔다"):
        integrate(scores, as_of=AS_OF, weights={"market": 1.0})


def test_identical_input_twice_is_identical_output() -> None:
    def _run() -> IntegratedSignal:
        scores = [
            _score("market", "232080", 0.42),
            _score("market", "069500", -0.13),
            _score("sentiment", "069500", 0.61),
            _score("sentiment", "232080", -0.77),
            _score("regime", "069500", 0.04),
            _score("regime", "232080", 0.29),
        ]
        return integrate(
            scores, as_of=AS_OF, weights={"market": 0.5, "sentiment": 0.3, "regime": 0.2}
        )

    first, second = _run(), _run()
    assert first == second
    assert first.model_dump() == second.model_dump()
    # 키 순서까지 같아야 JSONB 로 넣었을 때 바이트가 같다.
    assert list(first.signals) == list(second.signals)
    assert list(first.per_view_scores) == list(second.per_view_scores)


def test_output_is_deterministically_ordered() -> None:
    # 입력 순서가 뒤섞여도 출력은 정렬된 순서로 나온다.
    shuffled = [
        _score("regime", "232080", 0.29),
        _score("market", "069500", -0.13),
        _score("regime", "069500", 0.04),
        _score("market", "232080", 0.42),
    ]
    result = integrate(shuffled, as_of=AS_OF, weights={"market": 0.5, "regime": 0.5})

    assert list(result.signals) == ["069500", "232080"]
    assert list(result.per_view_scores) == ["market", "regime"]
    assert list(result.view_weights_used) == ["market", "regime"]


def test_as_of_is_carried_through() -> None:
    assert _single_view(0.5).as_of == AS_OF
