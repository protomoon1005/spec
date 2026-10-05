"""spec_id → 러너 입력 조립.

scripts/run_backtest_vbt.py 가 하드코딩 7종목과 data/prices.csv 로 만드는 입력을
DB 의 전략서·종목 원장·가격으로 같은 형태로 만든다. 러너 알고리즘은 건드리지
않는다 — 여기서는 모양만 맞춘다.

리밸런싱 일정 대응 (2026-09-28 결정 D3-a). 러너의 평가 시점은 주간(각 주 마지막
거래일)으로 고정이라 리밸런싱도 그 부분집합만 가능하다. 정확히 대응되는 두 규칙만
받고 나머지는 추정하지 않고 거부한다.
  calendar/monthly/day=1 → monthly_first(주간)   그 달 첫 주간 평가일
  calendar/weekly/day=1  → 주간 전부             월요일 대신 주 마지막 거래일
min_interval_days 는 앞 리밸런싱일과의 간격이 모자란 날짜를 건너뛰는 것으로
적용한다(FN-506 최소 간격 검사). 러너의 주간 구분이 해를 넘는 주를 둘로 나눠서
주간 규칙은 연말에 3~4일 간격이 생긴다.

검증 결과를 쓴다 (2026-10-05). 마지막 검증(validation_logs)이 4단까지 통과했으면
  비중 범위   spec_universe 의 확정값(weight_min · weight_max) — 범위 보정 · 하드캡 반영
  현금        3단 adjusted_bounds.cash_target — 상한을 다 늘려도 모자라면 성향 기본값보다 크다
  최소 간격   4단 clamped_fields 의 rebalance.min_interval_days 적용값(하드캡으로 올린 값)
를 쓴다. 검증 전이거나 막힌 전략서는 예전처럼 AI 원래 범위(_raw)를 러너의 resolve_bounds 로
접고 성향 기본 현금을 쓴다 — 막을지는 팀이 정할 일이라 막지 않고, 무엇을 썼는지
validation 에 남긴다(status: passed · failed · none).
known-issues "백테스트가 Validator 결과를 쓰지 않음" 의 비중 범위 · 현금 · 간격 부분이다.
낙폭 · 1회 손실 같은 클램프된 제약은 러너가 쓰는 칸이 아니라 여기서도 다루지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from app.repositories import etf_master, price_daily, specs

from .runner import monthly_first, weekly_dates

# scripts/run_backtest_vbt.py 의 MARKET_TICKER 와 같아야 한다. 시장 종목은 평가 날짜
# 격자와 매수 후 보유 비교에 쓰이므로 유니버스에 없어도 읽는다.
# 기간 기본값(데모 계획 1.4)은 service.DEFAULT_PERIOD_START 한 곳에 둔다.
MARKET_TICKER = "069500"

# (type, freq, day) → (일정 함수, 화면에 적을 근사 설명)
_SCHEDULES = {
    ("calendar", "monthly", 1): (
        monthly_first,
        "매달 첫 거래일 → 그 달 첫 주간 평가일(주 마지막 거래일)로 근사",
    ),
    ("calendar", "weekly", 1): (list, "매주 월요일 → 각 주 마지막 거래일로 근사"),
}


class InputError(Exception):
    """조립을 멈춘 이유. 메시지는 사용자에게 그대로 보여 줄 수 있는 문장이다."""


@dataclass(frozen=True)
class BacktestInputs:
    spec_id: str
    risk_level: int
    holdings: list[dict]
    prices_wide: pd.DataFrame
    valuation_dates: list[str]
    rebalance_dates: list[str]
    rebalance_rule: dict
    schedule_note: str
    # 최소 간격에 걸려 건너뛴 리밸런싱 후보일
    skipped_rebalance_dates: list[str] = field(default_factory=list)
    # spec_universe 의 확정 범위 {종목: (하한, 상한)}. 검증 전에는 _raw 와 같다(M1 이 둘을 같이 쓴다).
    # holdings 는 데모 스크립트와 같은 모양으로 두려고 여기 따로 둔다
    final_ranges: dict[str, tuple[float, float]] = field(default_factory=dict)
    # 러너에 넘길 확정 범위. 검증을 통과했을 때만 final_ranges, 아니면 None(러너가 _raw 로 접는다)
    bounds: dict[str, tuple[float, float]] | None = None
    # 현금 하한. None 이면 러너가 성향 기본값(policy.CASH_MIN)을 쓴다
    cash_min: float | None = None
    # 무엇을 썼는지. {"status": passed|failed|none, "cash_target", "min_interval_days"}
    validation: dict = field(default_factory=lambda: {"status": "none"})

    @property
    def data_snapshot_asof(self) -> str:
        # 실제로 쓴 가장 늦은 거래일
        return self.valuation_dates[-1]


def build_inputs(spec_id: str, *, period_start: date, period_end: date) -> BacktestInputs:
    if period_start > period_end:
        raise InputError(f"기간이 거꾸로다: {period_start} ~ {period_end}")

    spec = specs.get_spec(spec_id)
    if spec is None:
        raise InputError(f"전략서가 없다: {spec_id}")
    risk_level = specs.get_spec_risk_level(spec_id)
    if risk_level is None:
        raise InputError(f"전략서의 성향 등급을 찾지 못했다: {spec_id}")
    # 가격을 읽기 전에 거른다 — 지원하지 않는 규칙이면 나머지는 볼 필요가 없다.
    rule = spec["rebalance"]
    schedule, note = _schedule(rule)

    holdings, final_ranges = _holdings(spec_id)
    tickers = [h["ticker"] for h in holdings]
    validation = _validation(spec_id)
    passed = validation["status"] == "passed"
    min_interval = validation.get("min_interval_days") if passed else None
    if min_interval is None:
        min_interval = rule["min_interval_days"]
    prices_wide = _prices_wide(sorted({*tickers, MARKET_TICKER}), as_of=period_end)

    start = period_start.isoformat()
    all_dates = [d for d in prices_wide[MARKET_TICKER].dropna().index if d >= start]
    if not all_dates:
        raise InputError(f"기간 안에 거래일이 없다: {period_start} ~ {period_end}")
    weekly = weekly_dates(all_dates)
    rebalance, skipped = _apply_min_interval(schedule(weekly), min_interval)

    return BacktestInputs(
        spec_id=spec_id,
        risk_level=risk_level,
        holdings=holdings,
        prices_wide=prices_wide,
        valuation_dates=weekly,
        rebalance_dates=rebalance,
        rebalance_rule=rule,
        schedule_note=note,
        skipped_rebalance_dates=skipped,
        final_ranges=final_ranges,
        bounds=final_ranges if passed else None,
        cash_min=validation.get("cash_target") if passed else None,
        validation=validation,
    )


def _validation(spec_id: str) -> dict:
    # 마지막 검증의 요약. 4단까지 전부 통과했을 때만 passed.
    logs = specs.latest_validation(spec_id)
    if not logs:
        return {"status": "none"}
    if len(logs) < 4 or not all(row["passed"] for row in logs):
        return {"status": "failed", "blocked_at": next((r["stage"] for r in logs if not r["passed"]), None)}
    by_stage = {row["stage"]: row for row in logs}
    summary: dict = {"status": "passed", "checked_at": by_stage[4]["checked_at"].isoformat()}
    adjusted = by_stage[3].get("adjusted_bounds") or {}
    if adjusted.get("cash_target") is not None:
        summary["cash_target"] = float(adjusted["cash_target"])
    for clamp in by_stage[4].get("clamped_fields") or []:
        if clamp.get("field") == "rebalance.min_interval_days":
            summary["min_interval_days"] = int(round(float(clamp["applied"])))
    return summary


def _holdings(spec_id: str) -> tuple[list[dict], dict[str, tuple[float, float]]]:
    # 키는 scripts/run_backtest_vbt.py 의 holdings 와 같다. 확정 범위는 따로 돌려준다.
    universe = specs.get_spec_universe(spec_id)
    if not universe:
        raise InputError(f"전략서에 종목이 없다: {spec_id}")
    records = etf_master.get_by_tickers([u["ticker"] for u in universe])

    holdings = []
    final_ranges: dict[str, tuple[float, float]] = {}
    for u in universe:
        rec = records[u["ticker"]]  # spec_universe.ticker 는 etf_master FK 라 항상 있다
        required = {
            "risk_tag": rec.risk_tag,
            "asset_group_id": rec.asset_group_id,
            "sector_group_id": rec.sector_group_id,
            "country_group_id": rec.country_group_id,
            "weight_min_raw": u["weight_min_raw"],
            "weight_max_raw": u["weight_max_raw"],
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            # 비어 있는 값을 추정하면 비중 상한이 조용히 풀린다(등급 없음 → 상한 1.0).
            raise InputError(f"{u['ticker']}: 값이 비어 있다 — {', '.join(missing)}")
        holdings.append(
            {
                "ticker": u["ticker"],
                "name": rec.name,
                "grade": rec.risk_tag,
                "asset_group": rec.asset_group_id,
                "sector_group": rec.sector_group_id,
                "country_group": rec.country_group_id,
                "min_raw": float(u["weight_min_raw"]),
                "max_raw": float(u["weight_max_raw"]),
            }
        )
        lo = u["weight_min"] if u["weight_min"] is not None else u["weight_min_raw"]
        hi = u["weight_max"] if u["weight_max"] is not None else u["weight_max_raw"]
        final_ranges[u["ticker"]] = (float(lo), float(hi))
    return holdings, final_ranges


def _prices_wide(tickers: list[str], *, as_of: date) -> pd.DataFrame:
    # (날짜 × 종목) 종가, ffill, 인덱스는 YYYY-MM-DD 문자열 — 스크립트의 wide 와 같은 형태.
    history = price_daily.get_close_history(tickers, as_of=as_of)
    empty = [t for t in tickers if t not in history]
    if empty:
        raise InputError(f"가격이 한 행도 없는 종목: {', '.join(empty)}")

    series = {
        t: pd.Series({d.isoformat(): close for d, close in rows}, dtype="float64")
        for t, rows in history.items()
    }
    wide = pd.DataFrame(series).sort_index().ffill()
    return wide[tickers]


def _schedule(rule: dict | None):
    given = rule or {}
    trigger = given.get("trigger") or {}
    key = (trigger.get("type"), trigger.get("freq"), trigger.get("day"))
    if key not in _SCHEDULES or "min_interval_days" not in given:
        raise InputError(f"러너가 지원하지 않는 리밸런싱 규칙: {rule}")
    return _SCHEDULES[key]


def _apply_min_interval(candidates: list[str], min_interval_days: int) -> tuple[list[str], list[str]]:
    kept: list[str] = []
    skipped: list[str] = []
    for d in candidates:
        if kept and (date.fromisoformat(d) - date.fromisoformat(kept[-1])).days < min_interval_days:
            skipped.append(d)
        else:
            kept.append(d)
    return kept, skipped
