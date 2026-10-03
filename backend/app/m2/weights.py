# 비중 산출 체인 RiskSizer → WeightMapper → GroupCapEnforcer (FN-501 ~ FN-503).
#
# 정본은 docs/m2-algorithms.md 2장(실행 순서·단계)과 "RiskSizer v0.1" 소절, 회귀 기준은 3장
# 1.5.3 검산 예제다. 수치와 순서를 여기서 바꾸지 않는다.
#
# ── 하늘 러너의 map_signals_to_weights 와 무엇이 다른가 ──────────────────
# 1~4단계(매핑·정규화·클램프 3회)는 같은 계산이다. 다른 점은 셋.
#   - 매핑 상한이 min(weight_max, RiskSizer 상한) 이다 (2장 "실행 순서가 고정되어 있다")
#   - 그룹캡 축소 뒤에도 각 종목이 weight_min 이상을 유지한다 (2장 5단계). 러너는 비례 축소만 한다
#   - 범위는 Validator 확정값을 받는다. 프리셋·하드캡을 여기서 다시 씌우지 않는다
# 러너 함수는 스크립트·TS 대조가 계속 쓰므로 그대로 두고, service 경로만 이 모듈을 쓴다.
#
# stdlib 만 쓴다 — CI 는 .[dev] 만 깔아서 numpy 를 끌어오면 이 테스트가 통째로 죽는다.
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from app.contracts.target_weights import GroupCapApplication, TargetWeights

# ── RiskSizer v0.1 상수 (docs/m2-algorithms.md 2장 소절, 2026-10-04 M2 결정) ──
# 바꾸면 버전을 올린다. 근거는 문서에 있다.
ATR_MULTIPLE = 2
HORIZON_DAYS = {"monthly": 21, "weekly": 5}  # 리밸런싱 사이 보유 기간(거래일)

# 2~4 정규화·클램프 반복 상한
MAX_ROUNDS = 3
# 합·경계 비교의 부동소수 여유
TOLERANCE = 1e-9

# 그룹캡 적용 순서. 상위(자산군) 먼저, 하위 나중 — 러너와 같은 순서다.
CAP_LEVELS = (("자산군", "asset_group"), ("국가", "country_group"), ("섹터", "sector_group"))


class WeightChainError(ValueError):
    """체인이 받을 수 없는 입력이거나 사후 검증이 깨졌다."""


@dataclass(frozen=True)
class AssetLimit:
    ticker: str
    weight_min: float  # Validator 확정값
    weight_max: float
    asset_group: str | None = None
    country_group: str | None = None
    sector_group: str | None = None


@dataclass(frozen=True)
class CapStep:
    """그룹캡 한 번 적용. 계약 ④ 의 GroupCapApplication 보다 많이 남긴다(계층·하한 고정 종목)."""

    level: str
    group_id: str
    sum_before: float
    cap: float
    scale_factor: float  # 축소 후 합 ÷ 축소 전 합
    floor_bound: tuple[str, ...]  # weight_min 에 고정된 종목
    cap_unmet: bool  # Σweight_min 이 캡보다 커서 캡까지 못 내렸다


@dataclass(frozen=True)
class WeightDecision:
    target: TargetWeights  # 계약 ④. mapped_weights = 정규화·클램프 후, weights = 그룹캡 후
    linear: dict[str, float]  # 1단계 선형 매핑 직후
    bounds: dict[str, tuple[float, float]]  # 매핑에 쓴 (lo, hi)
    risk_caps: dict[str, float | None]
    rounds: int  # 값이 바뀐 정규화·클램프 횟수
    # 3회 안에 수렴하지 못해 Σw > 1 − cash 가 남았을 때의 최종 보정. 없으면 None (2장 4단계 보완)
    normalize_fix: CapStep | None
    steps: tuple[CapStep, ...]


# --- RiskSizer -------------------------------------------------------------


def horizon_for(rule: Mapping | None) -> int:
    """리밸런싱 규칙 → 보유 기간 h(거래일). v0.1 은 달력 월간·주간만 정의돼 있다."""
    trigger = (rule or {}).get("trigger") or {}
    freq = trigger.get("freq")
    if trigger.get("type") != "calendar" or freq not in HORIZON_DAYS:
        raise WeightChainError(f"RiskSizer v0.1 에 보유 기간이 정의되지 않은 리밸런싱 규칙: {rule}")
    return HORIZON_DAYS[freq]


def risk_caps(
    atr_pct: Mapping[str, float | None],
    *,
    loss_limit: float,
    horizon_days: int,
    k: float = ATR_MULTIPLE,
) -> dict[str, float | None]:
    """종목별 상한 L / (k × ATR% × √h). ATR 이 없거나 0 이하면 상한 없음(None)."""
    out: dict[str, float | None] = {}
    for ticker, atr in atr_pct.items():
        if atr is None or not math.isfinite(atr) or atr <= 0:
            out[ticker] = None
        else:
            out[ticker] = loss_limit / (k * atr * math.sqrt(horizon_days))
    return out


# --- WeightMapper · GroupCapEnforcer --------------------------------------


def compute_target_weights(
    assets: Sequence[AssetLimit],
    signals: Mapping[str, float],
    *,
    as_of: date,
    cash_min: float,
    caps: Mapping[str, float],
    risk_caps: Mapping[str, float | None] | None = None,
    exempt: frozenset[str] | set[str] = frozenset(),
) -> WeightDecision:
    """신호 → 목표 비중. cash_min 은 Validator 의 현금 목표다(case B 면 성향 기본값보다 클 수 있다)."""
    if not assets:
        raise WeightChainError("종목이 하나도 없다")
    tickers = [a.ticker for a in assets]
    if len(set(tickers)) != len(tickers):
        raise WeightChainError(f"같은 종목이 두 번 들어왔다: {tickers}")
    investable = 1.0 - cash_min
    given = dict(risk_caps or {})

    # 0 RiskSizer 결합. 상한이 하한보다 작으면 하한을 내린다(한도가 우선, resolve_bounds 와 같은 규칙).
    bounds: dict[str, tuple[float, float]] = {}
    for a in assets:
        hi = a.weight_max
        cap = given.get(a.ticker)
        if cap is not None:
            hi = min(hi, cap)
        bounds[a.ticker] = (min(a.weight_min, hi), hi)
    sum_lo = math.fsum(lo for lo, _ in bounds.values())
    if sum_lo > investable + TOLERANCE:
        # 1장 불변식은 Validator 가 보장한다. 여기 걸리면 확정값이 아닌 걸 넣은 것이다.
        raise WeightChainError(f"Σweight_min {sum_lo:.6f} 가 투자 가능 금액 {investable:.6f} 를 넘는다")

    for t in tickers:
        if t not in signals:
            raise WeightChainError(f"{t}: 신호가 없다")
        if not -1.0 - TOLERANCE <= signals[t] <= 1.0 + TOLERANCE:
            raise WeightChainError(f"{t}: 신호가 -1~+1 밖이다: {signals[t]}")

    # 1 선형 매핑
    linear = {t: bounds[t][0] + (signals[t] + 1) / 2 * (bounds[t][1] - bounds[t][0]) for t in tickers}

    # 2~4 정규화(양방향) + 클램프, 최대 3회, 변화가 없으면 조기 종료
    w = dict(linear)
    rounds = 0
    for _ in range(MAX_ROUNDS):
        total = math.fsum(w.values())
        if total <= 0:
            break
        nxt = {t: min(max(w[t] * investable / total, bounds[t][0]), bounds[t][1]) for t in tickers}
        if all(abs(nxt[t] - w[t]) <= TOLERANCE for t in tickers):
            break
        w = nxt
        rounds += 1
    # 4' 최종 보정 (2026-10-04 M2 결정, docs/m2-algorithms.md 2장 보완 규칙)
    # 클램프가 종목을 weight_min 으로 끌어올리면 합이 다시 target 을 넘고, 3회 안에 수렴하지 않으면
    # 6단계 "현금 ≥ cash_min" 이 깨진다. 그룹캡과 같은 하한 고정 비례 축소를 전체에 한 번 건다.
    # Σweight_min ≤ target 은 위에서 확인했으므로 항상 target 까지 내려간다.
    normalize_fix = None
    total = math.fsum(w.values())
    if total > investable + TOLERANCE:
        floors = {t: bounds[t][0] for t in tickers}
        normalize_fix = _shrink(w, tickers, floors, "정규화", "*", total, investable)
    mapped = dict(w)

    # 5 그룹캡
    steps: list[CapStep] = []
    for level, attr in CAP_LEVELS:
        for g, members in _groups(assets, attr).items():
            if g in exempt or g not in caps:
                continue
            before = math.fsum(w[t] for t in members)
            if before <= caps[g] + TOLERANCE:
                continue
            steps.append(_shrink(w, members, {t: bounds[t][0] for t in members}, level, g, before, caps[g]))

    # 6 잔여 → 현금
    cash = 1.0 - math.fsum(w.values())
    _post_check(assets, w, bounds, cash, cash_min, caps, exempt, steps)

    target = TargetWeights(
        as_of=as_of,
        weights=w,
        cash=min(max(cash, 0.0), 1.0),
        mapped_weights=mapped,
        group_cap_applications=[
            GroupCapApplication(
                group_id=s.group_id, sum_before=s.sum_before, cap=s.cap, scale_factor=s.scale_factor
            )
            for s in steps
        ],
    )
    return WeightDecision(
        target=target,
        linear=linear,
        bounds=bounds,
        risk_caps={t: given.get(t) for t in tickers},
        rounds=rounds,
        normalize_fix=normalize_fix,
        steps=tuple(steps),
    )


def _groups(assets: Sequence[AssetLimit], attr: str) -> dict[str, list[str]]:
    # 계층 attr 의 그룹 → 종목. 그룹은 처음 나온 순서, 그룹 없는 종목은 빠진다.
    out: dict[str, list[str]] = {}
    for a in assets:
        g = getattr(a, attr)
        if g is not None:
            out.setdefault(g, []).append(a.ticker)
    return out


def _shrink(
    w: dict[str, float],
    members: list[str],
    lo: dict[str, float],
    level: str,
    group: str,
    before: float,
    cap: float,
) -> CapStep:
    # 비례 축소하되 weight_min 아래로 떨어지는 종목은 하한에 고정하고, 남은 예산을 나머지에 다시 나눈다.
    floor_sum = math.fsum(lo.values())
    if floor_sum >= cap - TOLERANCE:
        # 하한만으로 캡을 채우거나 넘는다. 하한을 깨지 않는다 — 캡 미충족으로 기록한다.
        for t in members:
            w[t] = lo[t]
        fixed = list(members)
    else:
        fixed = []
        while True:
            free = [t for t in members if t not in fixed]
            budget = cap - math.fsum(lo[t] for t in fixed)
            factor = budget / math.fsum(w[t] for t in free)
            newly = [t for t in free if w[t] * factor < lo[t] - TOLERANCE]
            if not newly:
                for t in free:
                    w[t] *= factor
                for t in fixed:
                    w[t] = lo[t]
                break
            fixed.extend(newly)
    after = math.fsum(w[t] for t in members)
    return CapStep(
        level=level,
        group_id=group,
        sum_before=before,
        cap=cap,
        scale_factor=after / before,
        floor_bound=tuple(sorted(fixed)),
        cap_unmet=after > cap + TOLERANCE,
    )


def _post_check(
    assets: Sequence[AssetLimit],
    w: Mapping[str, float],
    bounds: Mapping[str, tuple[float, float]],
    cash: float,
    cash_min: float,
    caps: Mapping[str, float],
    exempt: frozenset[str] | set[str],
    steps: Sequence[CapStep],
) -> None:
    # 3장 "사후 검증": 범위 · 그룹 상한 · 현금 하한을 자동 검사한다.
    for t, (lo, hi) in bounds.items():
        if not lo - TOLERANCE <= w[t] <= hi + TOLERANCE:
            raise WeightChainError(f"{t}: {w[t]:.6f} 가 범위 [{lo:.6f}, {hi:.6f}] 밖이다")
    unmet = {s.group_id for s in steps if s.cap_unmet}
    for _, attr in CAP_LEVELS:
        for g, members in _groups(assets, attr).items():
            if g in exempt or g not in caps or g in unmet:
                continue
            total = math.fsum(w[t] for t in members)
            if total > caps[g] + TOLERANCE:
                raise WeightChainError(f"그룹 {g}: {total:.6f} 가 상한 {caps[g]} 를 넘는다")
    if cash < cash_min - TOLERANCE:
        raise WeightChainError(f"현금 {cash:.6f} 가 하한 {cash_min} 미만이다")
