"""FN-304 범위 보정 — 순수 함수 테스트. DB 없이 돈다.

무작위 전략서 1000건을 Validator 의 순수 파이프라인(run_stages)에 그대로 넣는다.
DB 왕복은 tests/test_m2_validator.py 가 본다.
"""

from __future__ import annotations

import random
from datetime import date

import pytest

from app.backtest.policy import HARDCAP, PRESET_GRADE_CAP
from app.m2.feasibility import BoundInput, fix_bounds
from app.m2.stages import Bound, EtfFacts, PriceCoverage, SpecContext, run_stages

TOL = 1e-9
N_RANDOM = 1000
SEED = 20261004

_COVERED = PriceCoverage(
    first_date=date(2023, 1, 2), last_date=date(2025, 12, 30), has_start=True, has_end=True
)


def _context(level: int, universe: list[tuple[str, str, float, float]], *, cash_min: float) -> SpecContext:
    """universe: (종목, 위험등급, weight_min_raw, weight_max_raw)."""
    grade_cap = PRESET_GRADE_CAP[level]
    payload = {
        "spec_id": "STR-random",
        "spec_version": "0.1",
        "user_id": 1,
        "name": "무작위 전략",
        "created_at": "2026-10-04T09:00:00+09:00",
        "universe": [
            {"ticker": t, "name": f"종목 {t}", "weight_min": lo, "weight_max": hi}
            for t, _, lo, hi in universe
        ],
        "rebalance": {"trigger": {"type": "calendar", "freq": "monthly", "day": 1}, "min_interval_days": 20},
        "signal_rules": {"market_analysis": {"indicators": ["ret_20"]}},
        "constraint": {
            "max_weight_per_asset": 0.30,
            "min_weight_per_asset": 0.00,
            "cash_min": cash_min,
            "max_loss_per_trade": 0.05,
            # 낙폭 모순 검사(3단)가 끼어들지 않게 넉넉히 둔다 — 여기서는 범위 보정만 본다.
            "max_drawdown": 1.0,
        },
    }
    return SpecContext(
        payload=payload,
        risk_level=level,
        bounds={tag: Bound(allowed_min=0.0, allowed_max=cap) for tag, cap in grade_cap.items()},
        hardcap={**HARDCAP, "hardcap_version": HARDCAP["version"]},
        etfs={
            t: EtfFacts(ticker=t, risk_tag=tag, is_leveraged=False, active=True, delisted_date=None)
            for t, tag, _, _ in universe
        },
        coverage={t: _COVERED for t, _, _, _ in universe},
        drawdowns={t: 0.20 for t, _, _, _ in universe},
        period_start=date(2023, 1, 1),
        as_of=date(2025, 12, 30),
    )


def _random_context(rng: random.Random, i: int) -> SpecContext:
    level = rng.randint(1, 5)
    grade_cap = PRESET_GRADE_CAP[level]
    tags = [tag for tag, cap in grade_cap.items() if cap > 0]
    # 하한을 크게 잡는 묶음이 있어야 case A 가 나온다. 저장된 전략서는 M1 이 Σmin+cash>1 을
    # 막아서 case A 가 안 나오므로 여기서 일부러 만든다.
    mode = rng.choice(("heavy_min", "light_max", "mixed"))
    universe = []
    for k in range(rng.randint(1, 8)):
        tag = rng.choice(tags)
        cap = grade_cap[tag]
        if mode == "light_max":
            hi = round(rng.uniform(0, min(cap, 0.15)), 2)
        else:
            hi = round(rng.uniform(0, cap), 2)
        lo = round(rng.uniform(0.5 * hi, hi) if mode == "heavy_min" else rng.uniform(0, hi), 2)
        universe.append((f"R{i:04d}-{k}", tag, min(lo, hi), hi))
    return _context(level, universe, cash_min=round(rng.uniform(0, 0.4), 2))


def test_random_1000_ranges_always_feasible():
    rng = random.Random(SEED)
    cases = {"A": 0, "B": 0, None: 0}
    passed = feasible = regenerations = 0

    for i in range(N_RANDOM):
        ctx = _random_context(rng, i)
        result = run_stages(ctx)

        if result.passed:
            passed += 1
        if result.regeneration.required:
            regenerations += 1
        case = result.stages[2].adjusted_bounds["case"]
        cases[case] += 1

        hard_cash = HARDCAP["cash_min"]
        cash_min = max(ctx.payload["constraint"]["cash_min"], hard_cash)
        investable = 1 - result.cash_target
        sum_min = sum(b.weight_min for b in result.universe)
        sum_max = sum(b.weight_max for b in result.universe)
        ok = (
            result.cash_target >= cash_min - TOL
            and sum_min <= investable + TOL
            and investable <= sum_max + TOL
        )
        feasible += ok
        assert ok, f"#{i} 불변식 깨짐: Σmin={sum_min} 1-cash={investable} Σmax={sum_max}"

        raw = {u["ticker"]: (u["weight_min"], u["weight_max"]) for u in ctx.payload["universe"]}
        for b in result.universe:
            assert (b.weight_min_raw, b.weight_max_raw) == raw[b.ticker], f"#{i} {b.ticker} _raw 변경"
            assert b.weight_min <= b.weight_max + TOL
            tag = ctx.etfs[b.ticker].risk_tag
            assert b.weight_max <= min(ctx.bounds[tag].allowed_max, HARDCAP["max_weight_per_asset"]) + TOL

    print(
        f"\n[범위 보정 {N_RANDOM}건] 실행가능 {feasible / N_RANDOM:.1%} · 통과 {passed / N_RANDOM:.1%}"
        f" · 재생성 신호 {regenerations}건"
        f" · case A {cases['A']} · case B {cases['B']} · 보정 없음 {cases[None]}"
    )
    assert feasible == N_RANDOM
    assert passed == N_RANDOM
    assert regenerations == 0
    assert cases["A"] >= 1 and cases["B"] >= 1


def test_same_input_twice_same_result():
    rng = random.Random(SEED)
    for i in range(50):
        ctx = _random_context(rng, i)
        assert run_stages(ctx) == run_stages(ctx)


def test_regression_example_needs_no_adjustment():
    # docs/m2-algorithms.md 3장 검산 예제: Σmin 0.40 ≤ 0.95 ≤ Σmax 1.40
    items = [
        BoundInput("069500", 0.10, 0.40, ceiling=0.40, floor=0.0),
        BoundInput("232080", 0.05, 0.25, ceiling=0.40, floor=0.0),
        BoundInput("133690", 0.10, 0.30, ceiling=0.40, floor=0.0),
        BoundInput("148070", 0.15, 0.45, ceiling=0.50, floor=0.0),
    ]
    result = fix_bounds(items, cash_min=0.05)
    assert result.case is None
    assert result.cash_target == pytest.approx(0.05)
    assert [(i.min_after, i.max_after) for i in result.items] == [
        (0.10, 0.40),
        (0.05, 0.25),
        (0.10, 0.30),
        (0.15, 0.45),
    ]


def test_case_a_scales_minimums():
    items = [
        BoundInput("A", 0.50, 0.60, ceiling=1.0, floor=0.0),
        BoundInput("B", 0.50, 0.60, ceiling=1.0, floor=0.0),
    ]
    result = fix_bounds(items, cash_min=0.10)
    assert result.case == "A"
    assert result.scale == pytest.approx(0.9)
    assert [i.min_after for i in result.items] == pytest.approx([0.45, 0.45])
    assert result.sum_min_after == pytest.approx(0.90)
    assert not result.reduce_universe


def test_case_a_floor_requests_smaller_universe():
    # allowed_min 바닥에 걸려 줄일 수 없으면 유니버스 축소를 요청한다.
    # 프리셋 v0.1 은 allowed_min 이 전부 0 이라 실제로는 안 나오는 분기다.
    items = [
        BoundInput("A", 0.50, 0.60, ceiling=1.0, floor=0.50),
        BoundInput("B", 0.50, 0.60, ceiling=1.0, floor=0.50),
    ]
    result = fix_bounds(items, cash_min=0.10)
    assert result.case == "A"
    assert result.reduce_universe


def test_case_b_expands_by_headroom():
    items = [
        BoundInput("A", 0.0, 0.20, ceiling=0.60, floor=0.0),
        BoundInput("B", 0.0, 0.20, ceiling=1.0, floor=0.0),
    ]
    result = fix_bounds(items, cash_min=0.10)
    assert result.case == "B"
    # 부족분 0.5 를 여유분 0.4 : 0.8 로 나눈다
    assert [i.max_after for i in result.items] == pytest.approx([0.20 + 0.5 / 3, 0.20 + 1.0 / 3])
    assert result.cash_target == pytest.approx(0.10)


def test_case_b_raises_cash_when_headroom_runs_out():
    items = [
        BoundInput("A", 0.0, 0.20, ceiling=0.30, floor=0.0),
        BoundInput("B", 0.0, 0.30, ceiling=0.30, floor=0.0),
    ]
    result = fix_bounds(items, cash_min=0.10)
    assert result.case == "B"
    assert [i.max_after for i in result.items] == pytest.approx([0.30, 0.30])
    assert result.cash_target == pytest.approx(0.40)


def test_ceiling_applies_before_check():
    # 하드캡(상한 0.30)이 반영된 값으로 불변식을 본다 (결정 1-A).
    items = [
        BoundInput("A", 0.0, 0.50, ceiling=0.30, floor=0.0),
        BoundInput("B", 0.0, 0.50, ceiling=0.30, floor=0.0),
    ]
    result = fix_bounds(items, cash_min=0.05)
    assert result.sum_max_before == pytest.approx(0.60)
    assert result.case == "B"
    assert result.cash_target == pytest.approx(0.40)
