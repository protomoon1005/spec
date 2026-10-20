"""service 경로 전용 M2 비중 체인 접합 — Validator 확정값 + RiskSizer ATR + 러너 weigh 콜러블.

알고리즘은 app/m2/weights.py · app/m2/orders.py 에 있다. 이 모듈은 입력을 모은다.

확정값을 어디서 얻나 (2026-10-20 통합 — 810665a 의 M2 결정과 c1f2501 의 비차단 정책을 합쳤다)
  approved 이후     마지막 검증 기록(validation_logs) + spec_universe 확정 범위를 그대로 읽는다.
                    승인 시점 기준이라 다시 계산하지 않는다(FN-307). 통과 기록이 없으면 실패 —
                    승인된 전략서에 통과 기록이 없는 건 정책 문제가 아니라 기록이 깨진 것이다.
  draft, 기록 있음   마지막 검증이 4단 통과면 그 값(사용자가 하드캡 화면에서 본 값)을 쓴다.
  draft, 기록 없음   Validator 순수 파이프라인을 메모리에서만 돌린다(기록 안 함). 통과면 그 값.
  그 밖(막힘)        확정값이 없다. 막지 않고 러너 기본 경로(_raw + resolve_bounds + 성향 기본 현금,
                    M2 체인 없음)로 돌린다. 막을지는 팀 결정이라(docs/known-issues.md)
                    FALLBACK_WHEN_BLOCKED 한 줄로 바꿀 수 있게 둔다.
M2 체인에는 확정값만 넣는다. 1장 불변식(Σweight_min ≤ 1 − cash)은 Validator 가 보장하는 것이라
_raw 를 넣으면 깨질 수 있다 — 성향 기본 현금보다 낮은 cash_min 을 쓴 전략서에서 실제로 깨진다.

무엇을 썼는지는 실행 기록 window_results.validation 에 남는다(프론트 백테스트 화면이 읽는다).
  {"status": "passed" | "failed", "source": "stored" | "recorded" | "revalidated", ...}
"none" 은 2026-10-20 이전 실행에만 있다(그때는 기록이 없으면 다시 검증하지 않았다).

ATR 은 M3 정의(atr_14_pct)를 그대로 쓴다. 리밸런싱일 이하 직전 FEATURE_WARMUP_ROWS 행만
잘라 compute_features 에 넣는다 — bridge 와 같은 절단 규칙이라 같은 날 같은 값이 나온다.
"""
from __future__ import annotations

import bisect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from app.m2.orders import build_orders
from app.m2.weights import AssetLimit, CapStep, compute_target_weights, risk_caps
from app.repositories import price_daily, specs
from app.views.market.features import FEATURE_WARMUP_ROWS, compute_features

DRAFT = "draft"
STORED_STATUSES = ("approved", "running", "closed")
STAGES = {1, 2, 3, 4}

# 검증에서 막힌 draft 를 막지 않고 러너 기본 경로로 돌린다(c1f2501 의 정책). 막을지는 팀 결정이다.
# False 로 바꾸면 막힌 전략서의 백테스트가 그 자리에서 실패한다 — 바꿀 곳은 이 한 줄이다.
FALLBACK_WHEN_BLOCKED = True


class ChainError(Exception):
    """체인 입력을 만들 수 없다. 메시지는 사용자에게 그대로 보여 줄 수 있는 문장이다."""


@dataclass(frozen=True)
class ConfirmedPolicy:
    source: str  # "stored" | "recorded" | "revalidated"
    hardcap_version: str
    bounds: dict[str, tuple[float, float]]  # 종목 → (weight_min, weight_max) 확정값
    cash_target: float
    max_loss_per_trade: float
    min_interval_days: int

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "hardcap_version": self.hardcap_version,
            "cash_target": self.cash_target,
            "max_loss_per_trade": self.max_loss_per_trade,
            "min_interval_days": self.min_interval_days,
        }


@dataclass(frozen=True)
class PolicyChoice:
    # None 이면 확정값이 없다(막힌 draft) — 러너 기본 경로로 돈다
    confirmed: ConfirmedPolicy | None
    validation: dict  # window_results.validation 에 그대로 들어간다


# --- 확정값 ------------------------------------------------------------------


def resolve_policy(spec_id: str, *, as_of: date) -> PolicyChoice:
    """백테스트가 쓸 확정값을 고른다(머리 주석의 표). as_of 는 백테스트 구간의 끝이다."""
    spec = specs.get_spec(spec_id)
    if spec is None:
        raise ChainError(f"전략서가 없다: {spec_id}")
    status = spec["status"]
    if status != DRAFT and status not in STORED_STATUSES:
        raise ChainError(f"알 수 없는 전략서 상태: {status}")

    logs = specs.latest_validation(spec_id)
    if status in STORED_STATUSES:
        if not _all_passed(logs):
            raise ChainError(f"{status} 전략서인데 통과한 검증 기록이 없다: {spec_id}")
        return _passed(_from_logs(spec_id, spec, logs, source="stored"), logs)
    if logs:
        if _all_passed(logs):
            return _passed(_from_logs(spec_id, spec, logs, source="recorded"), logs)
        blocked_at = next((r["stage"] for r in logs if not r["passed"]), None)
        reasons = [v.get("message", "") for r in logs for v in (r["violations"] or [])]
        return _blocked(blocked_at, reasons, source="recorded")
    return _revalidate(spec_id, spec, as_of=as_of)


def _all_passed(logs: Sequence[Mapping]) -> bool:
    return {r["stage"] for r in logs} == STAGES and all(r["passed"] for r in logs)


def _passed(confirmed: ConfirmedPolicy, logs: Sequence[Mapping] = ()) -> PolicyChoice:
    validation = {
        "status": "passed",
        "source": confirmed.source,
        "cash_target": confirmed.cash_target,
        "min_interval_days": confirmed.min_interval_days,
    }
    if logs:
        validation["checked_at"] = logs[-1]["checked_at"].isoformat()
    return PolicyChoice(confirmed=confirmed, validation=validation)


def _blocked(blocked_at: int | None, reasons: Sequence[str], *, source: str) -> PolicyChoice:
    if not FALLBACK_WHEN_BLOCKED:
        raise ChainError(f"Validator {blocked_at}단에서 막힌 전략서다: " + "; ".join(list(reasons)[:3]))
    return PolicyChoice(
        confirmed=None, validation={"status": "failed", "source": source, "blocked_at": blocked_at}
    )


def _revalidate(spec_id: str, spec: dict, *, as_of: date) -> PolicyChoice:
    # validator 는 service 를 import 하므로(DEFAULT_PERIOD_START) 함수 안에서 부른다.
    from app.m2.stages import run_stages
    from app.m2.validator import load_context

    try:
        ctx = load_context(spec_id, as_of=as_of)
    except LookupError as exc:  # 성향 기록 · 활성 하드캡이 없다
        raise ChainError(f"검증 입력을 만들 수 없다: {exc}") from exc
    result = run_stages(ctx)
    if not result.passed:
        reasons = [v.message for s in result.stages for v in s.violations]
        return _blocked(result.blocked_at, reasons, source="revalidated")
    stage4 = next(s for s in result.stages if s.stage == 4)
    applied = _applied(stage4.clamped_fields or ())
    return _passed(
        ConfirmedPolicy(
            source="revalidated",
            hardcap_version=result.hardcap_version,
            bounds={b.ticker: (b.weight_min, b.weight_max) for b in result.universe},
            cash_target=float(result.cash_target),
            max_loss_per_trade=_constraint(spec, applied, "max_loss_per_trade"),
            min_interval_days=_interval(spec, applied),
        )
    )


def _from_logs(spec_id: str, spec: dict, logs: Sequence[Mapping], *, source: str) -> ConfirmedPolicy:
    by_stage = {row["stage"]: row for row in logs}
    adjusted = by_stage[3]["adjusted_bounds"] or {}
    if adjusted.get("cash_target") is None:
        raise ChainError(f"검증 기록에 현금 목표가 없다: {spec_id}")
    universe = specs.get_spec_universe(spec_id)
    if any(u["weight_min"] is None or u["weight_max"] is None for u in universe):
        raise ChainError(f"확정 비중 범위가 비어 있다: {spec_id}")
    applied = _applied(by_stage[4]["clamped_fields"] or ())
    return ConfirmedPolicy(
        source=source,
        hardcap_version=spec["hardcap_version"],
        bounds={u["ticker"]: (float(u["weight_min"]), float(u["weight_max"])) for u in universe},
        cash_target=float(adjusted["cash_target"]),
        max_loss_per_trade=_constraint(spec, applied, "max_loss_per_trade"),
        min_interval_days=_interval(spec, applied),
    )


def _applied(clamped_fields: Sequence[Mapping]) -> dict[str, float]:
    # 4단 clamped_fields: {field, requested, applied, limit}. 하드캡으로 바뀐 칸만 있다.
    return {f["field"]: f["applied"] for f in clamped_fields}


def _constraint(spec: dict, applied: Mapping, name: str) -> float:
    key = f"constraint.{name}"
    if key in applied:
        return float(applied[key])
    value = (spec["constraint_user"] or {}).get(name)
    if value is None:
        raise ChainError(f"전략서 제약에 {name} 가 없다: {spec['spec_id']}")
    return float(value)


def _interval(spec: dict, applied: Mapping) -> int:
    key = "rebalance.min_interval_days"
    value = applied[key] if key in applied else (spec["rebalance"] or {})["min_interval_days"]
    return int(round(float(value)))


# --- ATR ---------------------------------------------------------------------


def load_bars(tickers: Sequence[str], *, as_of: date) -> dict[str, list[dict]]:
    return price_daily.get_bar_history(list(tickers), as_of=as_of)


class AtrSource:
    """리밸런싱일별 atr_14_pct. 워밍업 미달·고가/저가 결측이면 None."""

    def __init__(self, bars: Mapping[str, list[dict]]) -> None:
        self._bars = dict(bars)
        self._dates = {t: [row["trade_date"] for row in rows] for t, rows in bars.items()}

    def atr_pct(self, ticker: str, as_of: date) -> float | None:
        rows = self._bars.get(ticker, [])
        end = bisect.bisect_right(self._dates.get(ticker, []), as_of)
        if end < FEATURE_WARMUP_ROWS:
            return None
        window = rows[end - FEATURE_WARMUP_ROWS : end]
        if any(row["high"] is None or row["low"] is None for row in window):
            return None
        # 거래량은 ATR 에 안 쓰인다. 결측을 0 으로 넣는 것은 compute_features 가 None 을 못 받아서다.
        window = [{**row, "volume": row["volume"] or 0.0} for row in window]
        return compute_features(window, as_of=as_of)["atr_14_pct"]


# --- 러너 weigh 콜러블 ---------------------------------------------------------


class M2Weigher:
    """runner.run(weigh=...) 에 끼우는 콜러블.

    계열(전략·대조군)마다 새로 만든다 — 주문을 내려고 직전 목표 비중을 들고 있다.
    """

    def __init__(
        self,
        holdings: Sequence[Mapping],
        confirmed: ConfirmedPolicy,
        *,
        caps: Mapping[str, float],
        exempt: frozenset[str] | set[str],
        horizon_days: int,
        atr: AtrSource,
    ) -> None:
        missing = [h["ticker"] for h in holdings if h["ticker"] not in confirmed.bounds]
        if missing:
            raise ChainError(f"확정 범위가 없는 종목: {', '.join(missing)}")
        self._assets = [_asset_limit(h, confirmed.bounds[h["ticker"]]) for h in holdings]
        self._confirmed = confirmed
        self._caps = dict(caps)
        self._exempt = frozenset(exempt)
        self._horizon = horizon_days
        self._atr = atr
        self._current: dict[str, float] = {}
        self.records: dict[str, dict] = {}

    def __call__(
        self, d: str, signals: Mapping[str, float]
    ) -> tuple[dict[str, float], dict[str, float], float, list[dict]]:
        # 반환은 러너 map_signals_to_weights 와 같은 4-튜플 (정규화 후, 그룹캡 후, 현금, 그룹캡 적용)
        day = date.fromisoformat(d)
        atr = {a.ticker: self._atr.atr_pct(a.ticker, day) for a in self._assets}
        caps = risk_caps(atr, loss_limit=self._confirmed.max_loss_per_trade, horizon_days=self._horizon)
        decision = compute_target_weights(
            self._assets,
            signals,
            as_of=day,
            cash_min=self._confirmed.cash_target,
            caps=self._caps,
            risk_caps=caps,
            exempt=self._exempt,
        )
        target = decision.target
        orders = build_orders(self._current, target)
        self._current = dict(target.weights)

        steps = [
            {
                "stage": s.level,
                "groupId": s.group_id,
                "sumBefore": s.sum_before,
                "cap": s.cap,
                "factor": s.scale_factor,
            }
            for s in decision.steps
        ]
        self.records[d] = {
            "atrPct": atr,
            "riskCaps": caps,
            # RiskSizer 상한이 weight_max 보다 작아 실제로 매핑 상한이 된 종목
            "riskCapBound": [
                a.ticker for a in self._assets if caps[a.ticker] is not None and caps[a.ticker] < a.weight_max
            ],
            "bounds": {t: [lo, hi] for t, (lo, hi) in decision.bounds.items()},
            "linear": decision.linear,
            "rounds": decision.rounds,
            "normalizeFix": _normalize_fix_record(decision.normalize_fix),
            "floorBound": {s.group_id: list(s.floor_bound) for s in decision.steps if s.floor_bound},
            "orders": [{"ticker": o.ticker, "side": o.side, "deltaWeight": o.delta_weight} for o in orders],
            "targetWeights": target.model_dump(mode="json"),
        }
        return dict(target.mapped_weights), dict(target.weights), float(target.cash), steps


def _asset_limit(holding: Mapping, bounds: tuple[float, float]) -> AssetLimit:
    weight_min, weight_max = bounds
    return AssetLimit(
        holding["ticker"],
        weight_min=weight_min,
        weight_max=weight_max,
        asset_group=holding["asset_group"],
        country_group=holding["country_group"],
        sector_group=holding["sector_group"],
    )


def _normalize_fix_record(fix: CapStep | None) -> dict | None:
    if fix is None:
        return None
    return {"sumBefore": fix.sum_before, "factor": fix.scale_factor, "floorBound": list(fix.floor_bound)}
