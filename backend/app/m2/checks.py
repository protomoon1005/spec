# Validator 1·2·3단 검사 (FN-301 · FN-302 · FN-303). 순수 함수.
#
# 3단 논리 검사는 하한 ≤ 상한을 제대로 보고, 낙폭 모순은 명백한 경우만 잡는 얇은 형태다.
# 성향 프리셋 허용범위 검사는 주제 변경(결정 3)으로 지웠다.
from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from pydantic import ValidationError

from app.contracts.spec import SpecV0_1
from app.m2.stages import PriceCoverage, SpecContext, Violation

WEIGHT_TOLERANCE = 1e-9
# "마지막 행이 구간 끝 직전 5거래일 이내" (2026-10-04 결정 3)
END_WINDOW_TRADING_DAYS = 5


# ── 1단 스키마 (FN-301) ─────────────────────────────────────────────
# M1 이 쓰는 Spec 모델을 그대로 쓴다. 새 모델을 만들지 않는다.


def check_schema(payload: dict) -> tuple[SpecV0_1 | None, list[Violation]]:
    try:
        return SpecV0_1(**payload), []
    except ValidationError as exc:
        return None, [
            Violation(
                code="SCHEMA",
                field=".".join(str(p) for p in err["loc"]),
                message=err["msg"],
                detail={"type": err["type"]},
            )
            for err in exc.errors()
        ]


# ── 2단 참조 (FN-302) ───────────────────────────────────────────────


def check_reference(spec: SpecV0_1, ctx: SpecContext) -> list[Violation]:
    out: list[Violation] = []
    for item in spec.universe:
        etf = ctx.etfs.get(item.ticker)
        if etf is None:
            out.append(
                Violation("UNKNOWN_TICKER", f"종목 원장에 없는 종목이다: {item.ticker}", ticker=item.ticker)
            )
            continue
        if not etf.active or (etf.delisted_date is not None and etf.delisted_date <= ctx.as_of):
            out.append(
                Violation(
                    "DELISTED",
                    f"상장폐지된 종목이다: {item.ticker}",
                    ticker=item.ticker,
                    detail={"delisted_date": _iso(etf.delisted_date)},
                )
            )
        coverage = ctx.coverage.get(item.ticker)
        if coverage is None or not coverage.ok:
            out.append(
                Violation(
                    "NO_PRICE_DATA",
                    f"{item.ticker}: 백테스트 구간 {ctx.period_start} ~ {ctx.as_of} 의 가격이 모자란다",
                    ticker=item.ticker,
                    detail={
                        "first_date": _iso(coverage.first_date) if coverage else None,
                        "last_date": _iso(coverage.last_date) if coverage else None,
                    },
                )
            )
    return out


def coverage_of(dates: Sequence[date], calendar: Sequence[date]) -> PriceCoverage:
    """종목의 거래일 목록을 구간 달력(구간 안의 전체 거래일)과 맞춰 본다.

    dates 와 calendar 는 둘 다 구간 [시작, 끝] 으로 잘려 오름차순이어야 한다.
    """
    if not dates or not calendar:
        return PriceCoverage(first_date=None, last_date=None, has_start=False, has_end=False)
    end_floor = calendar[max(0, len(calendar) - END_WINDOW_TRADING_DAYS)]
    return PriceCoverage(
        first_date=dates[0],
        last_date=dates[-1],
        has_start=dates[0] == calendar[0],
        has_end=dates[-1] >= end_floor,
    )


def max_drawdown(closes: Sequence[float]) -> float | None:
    """종가열의 최대낙폭. 양수로 돌려준다(0.40 = 고점 대비 40% 하락). 없으면 None."""
    peak = None
    worst = 0.0
    for close in closes:
        if close is None or close <= 0:
            continue
        peak = close if peak is None else max(peak, close)
        worst = max(worst, 1 - close / peak)
    return worst if peak is not None else None


# ── 3단 논리 (FN-303) ───────────────────────────────────────────────


def check_logic(spec: SpecV0_1, ctx: SpecContext, *, cash_min: float) -> list[Violation]:
    """cash_min 은 범위 보정이 쓰는 값과 같은 것(사용자가 말한 cash_min)."""
    out: list[Violation] = []

    for item in spec.universe:
        # (1) 하한 ≤ 상한
        if item.weight_min > item.weight_max + WEIGHT_TOLERANCE:
            out.append(
                Violation(
                    "MIN_GT_MAX",
                    f"{item.ticker}: 하한이 상한보다 크다 ({item.weight_min} > {item.weight_max})",
                    ticker=item.ticker,
                    field="weight_min",
                )
            )


    # (2) 낙폭 목표 ↔ 유니버스 변동성. 투자 가능 금액 전부를 가장 덜 빠진 종목에 넣어도
    #     구간 최대낙폭이 목표를 넘으면 명백한 모순이다. 분산 효과까지 따지면 엄밀한
    #     하한은 아니라서 "명백한" 경우만 잡는 얇은 규칙이다.
    known = {t: dd for t in (i.ticker for i in spec.universe) if (dd := ctx.drawdowns.get(t)) is not None}
    if known:
        least_ticker = min(known, key=lambda t: (known[t], t))
        least_drawdown = known[least_ticker]
        invested = 1 - cash_min
        floor = invested * least_drawdown
        target = spec.constraint.max_drawdown
        if floor > target + WEIGHT_TOLERANCE:
            out.append(
                Violation(
                    "DRAWDOWN_INFEASIBLE",
                    f"낙폭 목표 {target:.0%} 를 지킬 수 없다 — 가장 덜 빠진 {least_ticker} 도 "
                    f"구간 최대낙폭이 {least_drawdown:.0%} 라 투자 비중 {invested:.0%} 만으로 "
                    f"{floor:.1%} 가 된다",
                    field="constraint.max_drawdown",
                    detail={
                        "max_drawdown": target,
                        "least_drawdown": least_drawdown,
                        "least_ticker": least_ticker,
                        "invested": invested,
                        "implied": floor,
                    },
                )
            )
    return out


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None
