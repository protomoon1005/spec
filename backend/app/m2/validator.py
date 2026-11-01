# 4단 Validator — 데이터베이스 쪽 (FN-301 ~ FN-306).
#
# 저장된 draft 전략서를 spec_id 로 읽어 순수 파이프라인(stages.run_stages)에 넣고,
# 결과를 spec_universe 확정값과 validation_logs 에 남긴다. 판정은 전부 stages 쪽에 있다.
#
# ── 구간 ─────────────────────────────────────────────────────────────
# as_of 는 오늘이 아니라 백테스트 구간의 끝이다. 구간은 DEFAULT_PERIOD_START 부터
# 가격 표의 마지막 거래일까지(2026-10-04 결정 3). period_end() 가 그 끝을 준다.
#
# ── 낙폭은 가격에서 계산한다 (결정 4) ─────────────────────────────────
# etf_master.mdd_3y 는 76종목 중 37종목만 채워져 있다(2026-10-04 확인). 비어 있는
# 종목만 가격으로 메우면 두 출처의 구간이 섞이므로, 전 종목을 구간 [시작, as_of] 의
# 종가로 계산한다. 저장소를 거치고 as_of 경계를 지킨다.
#
# ── 계층을 끈 실행은 기록하지 않는다 ───────────────────────────────────
# 발표용 실험이다. 결과가 검증 이력이나 확정 범위에 섞이면 안 된다.
from __future__ import annotations

from datetime import date

from app.backtest.service import DEFAULT_PERIOD_START
from app.m2 import checks
from app.m2.stages import (
    ALL_ON,
    FAILED,
    PASSED,
    Bound,
    EtfFacts,
    SpecContext,
    Stages,
    ValidationResult,
    run_stages,
)
from app.repositories import etf_master, presets, specs
from app.repositories import price_daily as prices


class SpecNotFound(LookupError):
    """전략서가 없다."""


def period_end() -> date | None:
    """검증 구간의 끝 = 가격 표의 마지막 거래일. 가격이 없으면 None."""
    return prices.get_latest_trade_date()


def validate(spec_id: str, *, as_of: date, stages: Stages = ALL_ON) -> ValidationResult:
    """전략서를 4단으로 검증한다. stages 를 하나라도 끄면 결과를 돌려주기만 하고 기록하지 않는다."""
    result = run_stages(load_context(spec_id, as_of=as_of), stages)
    if not stages.all_on:
        return result

    bounds = None
    if result.bounds_resolved:
        bounds = [
            {
                "ticker": b.ticker,
                "weight_min": b.weight_min,
                "weight_max": b.weight_max,
                "was_adjusted": b.was_adjusted,
            }
            for b in result.universe
        ]
    logs = [
        {
            "stage": s.stage,
            "passed": s.status == PASSED,
            "violations": [v.as_dict() for v in s.violations],
            "adjusted_bounds": s.adjusted_bounds,
            "clamped_fields": list(s.clamped_fields) if s.clamped_fields is not None else None,
        }
        for s in result.stages
        if s.status in (PASSED, FAILED)
    ]
    specs.record_validation(spec_id, bounds=bounds, logs=logs)
    return result


def load_context(spec_id: str, *, as_of: date) -> SpecContext:
    spec = specs.get_spec(spec_id)
    if spec is None:
        raise SpecNotFound(spec_id)
    profile = specs.get_spec_profile(spec_id)
    if profile is None:
        raise SpecNotFound(f"{spec_id}: 성향 기록이 없다")
    risk_level, preset_version = profile
    hardcap = presets.get_active_hardcap()
    if hardcap is None:
        raise LookupError("활성 하드캡이 없다 — db/seeds/01_hardcap_v0_1.sql 적재 여부를 확인할 것")

    # 비중은 _raw 에서 출발한다. 확정값(weight_min/max)은 지난 검증의 산물이라 입력이 아니다.
    universe = specs.get_spec_universe(spec_id)
    tickers = [u["ticker"] for u in universe]
    payload = {
        "spec_id": spec["spec_id"],
        "spec_version": spec["spec_version"],
        "user_id": spec["user_id"],
        "name": spec["name"],
        "created_at": spec["created_at"],
        "universe": [
            {
                "ticker": u["ticker"],
                "name": u["name"],
                "weight_min": _num(u["weight_min_raw"]),
                "weight_max": _num(u["weight_max_raw"]),
            }
            for u in universe
        ],
        "rebalance": spec["rebalance"],
        "signal_rules": spec["signal_rules"],
        "constraint": spec["constraint_user"],
    }

    start = DEFAULT_PERIOD_START
    calendar = prices.get_trade_dates(start=start, as_of=as_of)
    history = prices.get_close_history(tickers, as_of=as_of)
    in_period = {t: [(d, c) for d, c in rows if d >= start] for t, rows in history.items()}

    return SpecContext(
        payload=payload,
        risk_level=risk_level,
        bounds={
            tag: Bound(allowed_min=b.allowed_min, allowed_max=b.allowed_max)
            for tag, b in presets.get_asset_bounds(risk_level, preset_version=preset_version).items()
        },
        hardcap=hardcap,
        etfs={
            t: EtfFacts(
                ticker=t,
                risk_tag=r.risk_tag,
                is_leveraged=r.is_leveraged,
                active=r.active,
                delisted_date=r.delisted_date,
            )
            for t, r in etf_master.get_by_tickers(tickers).items()
        },
        coverage={t: checks.coverage_of([d for d, _ in in_period.get(t, [])], calendar) for t in tickers},
        drawdowns={t: checks.max_drawdown([c for _, c in in_period.get(t, [])]) for t in tickers},
        period_start=start,
        as_of=as_of,
    )


def _num(value) -> float | None:
    return float(value) if value is not None else None
