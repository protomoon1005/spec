# 4단 Validator 의 순수 파이프라인 (docs/m2-algorithms.md 6장, FN-301 ~ FN-306).
#
# 데이터베이스를 모른다. 필요한 사실은 전부 SpecContext 로 받는다 — 그래야 무작위
# 전략서 1000건을 DB 없이 같은 함수로 돌릴 수 있고, 같은 입력에 같은 결과가 나온다.
# DB 를 읽고 결과를 쓰는 쪽은 validator.py 다.
#
# ── 순서는 고정이다 ───────────────────────────────────────────────────
#   1 스키마 → 2 참조 → 3 논리 → (범위 보정, 3단 기록) → 4 하드캡 클램프
# 1·2·3단 중 하나가 실패하면 거기서 멈춘다. 뒤 단계는 not_run 이다.
#
# ── 하드캡은 범위 보정 입력에 이미 들어간다 (2026-10-04 결정 1-A) ────
# 보정이 클램프보다 먼저 돌기 때문에, 보정이 하드캡을 모르면 4단에서 상한을 깎는
# 순간 불변식이 다시 깨진다. 그래서 보정의 종목 상한은 min(allowed_max, 하드캡),
# 현금 하한은 max(cash_min, 하드캡 cash_min) 이다. 4단은 요청값과 적용값을 비교해
# clamped_fields 에 남긴다. **4단을 끄면 보정도 하드캡을 빼고 계산한다** — 계층 끄기
# 실험에서 하드캡 위반이 실제로 새어 나가야 하기 때문이다.
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import date

PASSED = "passed"
FAILED = "failed"
NOT_RUN = "not_run"  # 앞 단계에서 막혀 돌지 않았다
SKIPPED = "skipped"  # 계층 끄기 실험으로 껐다 — API 로는 나가지 않는다

STAGE_NAMES = {1: "schema", 2: "reference", 3: "logic", 4: "hardcap"}


@dataclass(frozen=True)
class Stages:
    """단계별 켜기/끄기. 발표의 계층 끄기 실험용이다. 기본은 전부 켠다."""

    schema: bool = True
    reference: bool = True
    logic: bool = True
    feasibility: bool = True
    hardcap: bool = True

    @property
    def all_on(self) -> bool:
        return all((self.schema, self.reference, self.logic, self.feasibility, self.hardcap))


ALL_ON = Stages()


@dataclass(frozen=True)
class Bound:
    """허용범위 프리셋 한 칸 (성향 × 위험등급)."""

    allowed_min: float
    allowed_max: float


@dataclass(frozen=True)
class EtfFacts:
    ticker: str
    risk_tag: str | None
    is_leveraged: bool
    active: bool
    delisted_date: date | None


@dataclass(frozen=True)
class PriceCoverage:
    """구간 [시작, 끝] 의 가격 보유 상태.

    has_start: 구간 시작 후 첫 거래일 행이 있다.
    has_end:   마지막 행이 구간 끝 직전 5거래일 이내다.
    """

    first_date: date | None
    last_date: date | None
    has_start: bool
    has_end: bool

    @property
    def ok(self) -> bool:
        return self.has_start and self.has_end


@dataclass(frozen=True)
class SpecContext:
    """Validator 가 보는 사실 전부. 비중은 전부 _raw(LLM 원출력)에서 출발한다."""

    payload: dict  # SpecV0_1 모양. universe 의 weight_min/max 는 _raw 값
    risk_level: int
    bounds: Mapping[str, Bound]  # 위험등급 → 허용범위
    hardcap: Mapping
    etfs: Mapping[str, EtfFacts]  # 없는 종목은 키가 없다
    coverage: Mapping[str, PriceCoverage]
    drawdowns: Mapping[str, float | None]  # 구간 최대낙폭, 양수(0.40 = 40% 하락)
    period_start: date
    as_of: date  # 구간 끝


@dataclass(frozen=True)
class Violation:
    code: str
    message: str
    ticker: str | None = None
    field: str | None = None
    detail: Mapping = dc_field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "ticker": self.ticker,
            "field": self.field,
            "detail": dict(self.detail),
        }


@dataclass(frozen=True)
class StageResult:
    stage: int
    status: str
    violations: tuple[Violation, ...] = ()
    adjusted_bounds: Mapping | None = None  # 3단만
    clamped_fields: tuple[Mapping, ...] | None = None  # 4단만

    @property
    def name(self) -> str:
        return STAGE_NAMES[self.stage]


@dataclass(frozen=True)
class FinalBound:
    ticker: str
    weight_min_raw: float
    weight_max_raw: float
    weight_min: float
    weight_max: float

    @property
    def was_adjusted(self) -> bool:
        return self.weight_min != self.weight_min_raw or self.weight_max != self.weight_max_raw


@dataclass(frozen=True)
class Regeneration:
    """FN-306. LLM 을 부르지 않는다 — 다시 만들어야 하는지와 그 사유만 돌려준다."""

    required: bool
    reasons: tuple[Mapping, ...] = ()


@dataclass(frozen=True)
class ValidationResult:
    spec_id: str
    hardcap_version: str
    as_of: date
    passed: bool
    blocked_at: int | None
    regeneration: Regeneration
    stages: tuple[StageResult, ...]
    universe: tuple[FinalBound, ...]
    # 보정이 정한 현금 목표. case B 에서 상한을 다 늘려도 모자라면 cash_min 보다 커진다.
    cash_target: float | None
    # 1~3단을 통과해 비중 범위가 확정됐는가. False 면 universe 는 _raw 그대로다.
    bounds_resolved: bool


def run_stages(ctx: SpecContext, stages: Stages = ALL_ON) -> ValidationResult:
    """스키마 → 참조 → 논리 → 범위 보정 → 하드캡 클램프. 순서를 바꾸지 않는다."""
    # 단계 모듈이 이 파일의 타입을 쓰므로 여기서 불러온다(순환 import 회피).
    from app.m2 import checks, feasibility, hardcap
    from app.m2.regenerate import regeneration_for

    results: list[StageResult] = []
    # _raw 는 스키마를 통과한 모델에서 꺼낸다. 그 전에 막히면 비중을 믿을 수 없어 비워 둔다.
    raw: tuple[FinalBound, ...] = ()

    def stop(blocked_at: int | None) -> ValidationResult:
        done = {r.stage for r in results}
        full = results + [StageResult(stage=s, status=NOT_RUN) for s in STAGE_NAMES if s not in done]
        return ValidationResult(
            spec_id=str(ctx.payload.get("spec_id")),
            hardcap_version=str(ctx.hardcap["hardcap_version"]),
            as_of=ctx.as_of,
            passed=False,
            blocked_at=blocked_at,
            regeneration=regeneration_for(full),
            stages=tuple(full),
            universe=raw,
            cash_target=None,
            bounds_resolved=False,
        )

    # 1 스키마 — 끄더라도 뒤 단계가 모델을 읽어야 하므로 형식이 맞아야 한다.
    spec, violations = checks.check_schema(ctx.payload)
    if spec is None:
        if not stages.schema:
            raise ValueError("스키마 단계를 끈 실험에는 형식이 맞는 전략서만 넣을 수 있다")
        results.append(StageResult(stage=1, status=FAILED, violations=tuple(violations)))
        return stop(1)
    results.append(StageResult(stage=1, status=PASSED if stages.schema else SKIPPED))
    raw = tuple(
        FinalBound(
            ticker=u.ticker,
            weight_min_raw=u.weight_min,
            weight_max_raw=u.weight_max,
            weight_min=u.weight_min,
            weight_max=u.weight_max,
        )
        for u in spec.universe
    )

    # 2 참조
    if stages.reference:
        violations = checks.check_reference(spec, ctx)
        results.append(
            StageResult(stage=2, status=FAILED if violations else PASSED, violations=tuple(violations))
        )
        if violations:
            return stop(2)
    else:
        results.append(StageResult(stage=2, status=SKIPPED))

    # 하드캡을 켰으면 보정과 논리 검사가 하드캡을 반영한 현금 하한을 쓴다(결정 1-A).
    cash_min = float(spec.constraint.cash_min)
    if stages.hardcap:
        cash_min = max(cash_min, float(ctx.hardcap["cash_min"]))

    # 3 논리
    logic_violations = checks.check_logic(spec, ctx, cash_min=cash_min) if stages.logic else []
    if logic_violations:
        results.append(StageResult(stage=3, status=FAILED, violations=tuple(logic_violations)))
        return stop(3)

    # 범위 보정 — 기록은 3단 행에 둔다
    bounds = raw
    adjusted = None
    cash_target = cash_min
    reduce_violation: list[Violation] = []
    if stages.feasibility:
        cap = float(ctx.hardcap["max_weight_per_asset"]) if stages.hardcap else 1.0
        inputs = []
        for b in raw:
            etf = ctx.etfs.get(b.ticker)
            bound = ctx.bounds.get(etf.risk_tag) if etf is not None and etf.risk_tag else None
            allowed_max = bound.allowed_max if bound else 1.0
            allowed_min = bound.allowed_min if bound else 0.0
            inputs.append(
                feasibility.BoundInput(
                    ticker=b.ticker,
                    weight_min_raw=b.weight_min_raw,
                    weight_max_raw=b.weight_max_raw,
                    ceiling=min(allowed_max, cap),
                    floor=allowed_min,
                )
            )
        fixed = feasibility.fix_bounds(inputs, cash_min=cash_min)
        adjusted = feasibility.as_log(fixed)
        cash_target = fixed.cash_target
        bounds = tuple(
            FinalBound(
                ticker=b.ticker,
                weight_min_raw=b.weight_min_raw,
                weight_max_raw=b.weight_max_raw,
                weight_min=item.min_after,
                weight_max=item.max_after,
            )
            for b, item in zip(raw, fixed.items)
        )
        if fixed.reduce_universe:
            # 재생성 사유가 아니다(FN-306) — 사용자에게 종목 수를 줄이라고 알린다.
            reduce_violation.append(
                Violation(
                    "UNIVERSE_TOO_LARGE",
                    "종목별 최소 비중을 허용 하한까지 줄여도 투자 가능 금액을 넘는다 — 종목 수를 줄여야 한다",
                    detail={"sum_min_after": fixed.sum_min_after, "investable": 1 - cash_min},
                )
            )

    if reduce_violation:
        status3 = FAILED
    elif stages.logic:
        status3 = PASSED
    else:
        status3 = SKIPPED
    results.append(
        StageResult(stage=3, status=status3, violations=tuple(reduce_violation), adjusted_bounds=adjusted)
    )
    if reduce_violation:
        return stop(3)

    # 4 하드캡 클램프
    blocked_at = None
    if stages.hardcap:
        clamped = hardcap.clamp(spec, bounds, hardcap=ctx.hardcap, etfs=ctx.etfs)
        bounds = clamped.universe
        results.append(
            StageResult(
                stage=4,
                status=FAILED if clamped.violations else PASSED,
                violations=clamped.violations,
                clamped_fields=clamped.clamped_fields,
            )
        )
        if clamped.violations:
            blocked_at = 4
    else:
        results.append(StageResult(stage=4, status=SKIPPED))

    return ValidationResult(
        spec_id=spec.spec_id,
        hardcap_version=str(ctx.hardcap["hardcap_version"]),
        as_of=ctx.as_of,
        passed=blocked_at is None,
        blocked_at=blocked_at,
        regeneration=regeneration_for(results),
        stages=tuple(results),
        universe=bounds,
        cash_target=cash_target,
        bounds_resolved=True,
    )
