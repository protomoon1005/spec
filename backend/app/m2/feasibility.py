# FN-304 범위 실행가능성 보정 (docs/m2-algorithms.md 1장).
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

# 합 비교의 부동소수 여유. 0.1 + 0.2 같은 값이 경계에서 case 를 뒤집지 않게 한다.
TOLERANCE = 1e-9


@dataclass(frozen=True)
class BoundInput:
    ticker: str
    weight_min_raw: float
    weight_max_raw: float
    ceiling: float  # min(allowed_max, 하드캡 종목 상한)
    floor: float  # allowed_min — case A 축소의 바닥


@dataclass(frozen=True)
class AdjustedItem:
    ticker: str
    ceiling: float
    min_before: float
    max_before: float
    min_after: float
    max_after: float


@dataclass(frozen=True)
class FeasibilityResult:
    case: str | None  # "A" · "B" · None(보정 불필요)
    cash_min: float
    cash_target: float
    scale: float | None  # case A 의 축소 계수
    sum_min_before: float
    sum_max_before: float
    sum_min_after: float
    sum_max_after: float
    items: tuple[AdjustedItem, ...]
    reduce_universe: bool  # case A 에서 바닥에 걸려 여전히 넘친다 → 유니버스 축소 요청


def fix_bounds(items: Sequence[BoundInput], *, cash_min: float) -> FeasibilityResult:
    """불변식 Σmin ≤ 1 − cash ≤ Σmax 를 맞춘다. 되묻지도 재생성하지도 않는다.

    **항상 _raw 에서 출발한다.** 앞서 보정한 값을 입력으로 받지 않으므로 몇 번을
    돌려도 같은 결과가 나온다.
    """
    target = 1 - cash_min
    # 종목 상한(프리셋 ∧ 하드캡)을 먼저 씌운다. 하한이 상한을 넘으면 상한까지 내린다.
    max_b = [min(i.weight_max_raw, i.ceiling) for i in items]
    min_b = [min(i.weight_min_raw, hi) for i, hi in zip(items, max_b)]
    sum_min, sum_max = sum(min_b), sum(max_b)

    case: str | None = None
    scale: float | None = None
    reduce_universe = False
    cash_target = cash_min
    min_a, max_a = list(min_b), list(max_b)

    if sum_min > target + TOLERANCE:
        # case A — 하한을 같은 비율로 줄이되 allowed_min 아래로는 내리지 않는다.
        case = "A"
        scale = target / sum_min
        min_a = [max(lo * scale, min(i.floor, lo)) for i, lo in zip(items, min_b)]
        reduce_universe = sum(min_a) > target + TOLERANCE
    elif sum_max < target - TOLERANCE:
        # case B — 상한 여유분(ceiling − 현재 상한)에 비례해 늘린다.
        case = "B"
        deficit = target - sum_max
        headroom = [i.ceiling - hi for i, hi in zip(items, max_b)]
        room = sum(headroom)
        if room > deficit:
            max_a = [hi + deficit * h / room for hi, h in zip(max_b, headroom)]
        else:
            # 늘려도 모자라면 전부 상한까지 올리고 남는 몫은 현금이 흡수한다.
            max_a = [i.ceiling for i in items]
            cash_target = 1 - sum(max_a)

    return FeasibilityResult(
        case=case,
        cash_min=cash_min,
        cash_target=cash_target,
        scale=scale,
        sum_min_before=sum_min,
        sum_max_before=sum_max,
        sum_min_after=sum(min_a),
        sum_max_after=sum(max_a),
        items=tuple(
            AdjustedItem(
                ticker=i.ticker,
                ceiling=i.ceiling,
                min_before=lo0,
                max_before=hi0,
                min_after=lo1,
                max_after=hi1,
            )
            for i, lo0, hi0, lo1, hi1 in zip(items, min_b, max_b, min_a, max_a)
        ),
        reduce_universe=reduce_universe,
    )


def as_log(result: FeasibilityResult) -> dict:
    """validation_logs.adjusted_bounds (3단 행) 에 그대로 들어가는 모양."""
    return {
        "case": result.case,
        "cash_min": result.cash_min,
        "cash_target": result.cash_target,
        "scale": result.scale,
        "sum_min_before": result.sum_min_before,
        "sum_max_before": result.sum_max_before,
        "sum_min_after": result.sum_min_after,
        "sum_max_after": result.sum_max_after,
        "reduce_universe": result.reduce_universe,
        "items": [
            {
                "ticker": i.ticker,
                "ceiling": i.ceiling,
                "min_before": i.min_before,
                "min_after": i.min_after,
                "max_before": i.max_before,
                "max_after": i.max_after,
            }
            for i in result.items
        ],
    }
