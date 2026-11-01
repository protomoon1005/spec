# FN-305 하드캡 클램프 (Validator 4단). 순수 함수.
#
# 활성 하드캡을 넘는 값을 하드캡으로 치환하고 clamped_fields 에 요청값·적용값을 남긴다.
# 하드캡은 Spec 에 자리가 없다 — 여기서 constraint_user 를 고치지 않는다(결정 2).
# 확정값은 validation_logs 4단 행의 clamped_fields 가 들고 있다.
#
# 리밸런싱 최소 간격도 여기서 하드캡 값으로 올린다(contracts/spec.py RebalanceRule,
# 하드캡 프로파일 초안의 clamp_direction: max). 리밸런싱을 하지 않는 전략
# (trigger.type == "none")은 간격이 의미가 없어 대상이 아니다.
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.contracts.spec import SpecV0_1
from app.m2.stages import EtfFacts, FinalBound, Violation

WEIGHT_TOLERANCE = 1e-9
NO_REBALANCE = "none"

# Spec 의 constraint 칸 → (하드캡 칸, 방향). "max" 는 하드캡이 상한, "min" 은 하한.
_CONSTRAINT_LIMITS = (
    ("max_weight_per_asset", "max_weight_per_asset", "max"),
    ("max_drawdown", "max_drawdown", "max"),
    ("max_loss_per_trade", "max_loss_per_trade", "max"),
    ("cash_min", "cash_min", "min"),
)


@dataclass(frozen=True)
class ClampResult:
    clamped_fields: tuple[dict, ...]
    violations: tuple[Violation, ...]
    universe: tuple[FinalBound, ...]


def clamp(
    spec: SpecV0_1,
    bounds: Sequence[FinalBound],
    *,
    hardcap: Mapping,
    etfs: Mapping[str, EtfFacts],
) -> ClampResult:
    """bounds 는 앞 단계(범위 보정)를 거친 값. _raw 는 요청값으로 비교에만 쓴다."""
    cap = float(hardcap["max_weight_per_asset"])
    fields: list[dict] = []
    final: list[FinalBound] = []

    for b in bounds:
        for name, requested in (("weight_min", b.weight_min_raw), ("weight_max", b.weight_max_raw)):
            if requested > cap + WEIGHT_TOLERANCE:
                fields.append(_field(f"universe.{b.ticker}.{name}", requested, cap, "max_weight_per_asset"))
        hi = min(b.weight_max, cap)
        final.append(
            FinalBound(
                ticker=b.ticker,
                weight_min_raw=b.weight_min_raw,
                weight_max_raw=b.weight_max_raw,
                weight_min=min(b.weight_min, hi),
                weight_max=hi,
            )
        )

    constraint = spec.constraint
    for name, limit_name, direction in _CONSTRAINT_LIMITS:
        requested = float(getattr(constraint, name))
        limit = float(hardcap[limit_name])
        over = (
            requested > limit + WEIGHT_TOLERANCE
            if direction == "max"
            else requested < limit - WEIGHT_TOLERANCE
        )
        if over:
            fields.append(_field(f"constraint.{name}", requested, limit, limit_name))

    interval = spec.rebalance.min_interval_days
    hard_interval = int(hardcap["min_interval_days"])
    if spec.rebalance.trigger.type != NO_REBALANCE and interval < hard_interval:
        fields.append(_field("rebalance.min_interval_days", interval, hard_interval, "min_interval_days"))

    violations: list[Violation] = []
    if not hardcap["leverage_allowed"]:
        for b in bounds:
            etf = etfs.get(b.ticker)
            if etf is not None and etf.is_leveraged:
                violations.append(
                    Violation(
                        "LEVERAGE_NOT_ALLOWED",
                        f"{b.ticker}: 레버리지·인버스 종목은 시스템 상한(하드캡)이 허용하지 않는다",
                        ticker=b.ticker,
                        detail={"hardcap_version": hardcap.get("hardcap_version")},
                    )
                )

    return ClampResult(clamped_fields=tuple(fields), violations=tuple(violations), universe=tuple(final))


def _field(field: str, requested: float, applied: float, limit: str) -> dict:
    return {"field": field, "requested": requested, "applied": applied, "limit": limit}
