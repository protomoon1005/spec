"""spec_id → 러너 입력 조립.

scripts/run_backtest_vbt.py 가 하드코딩 7종목과 data/prices.csv 로 만드는 입력을
DB 의 전략서·종목 원장·가격으로 같은 형태로 만든다. 러너 알고리즘은 건드리지
않는다 — 여기서는 모양만 맞춘다.

리밸런싱 일정 대응 (2026-09-28 결정 D3-a, 2026-10-27 BBL 6블록으로 확장). 러너의 평가 시점은
주간(각 주 마지막 거래일)으로 고정이라 리밸런싱도 그 부분집합만 가능하다. 달력형은 아래처럼
주간 평가일로 근사하고, 표에 없는 규칙은 추정하지 않고 거부한다.
  calendar/monthly/day=1    → 그 달 첫 주간 평가일                         (RB_MONTHLY_FIRST)
  calendar/monthly/day=15   → 그 달 15일 이후 첫 주간 평가일, 없으면 그 달 없음 (RB_MONTHLY_MID)
  calendar/quarterly/day=1  → 분기(1·4·7·10월) 첫 주간 평가일              (RB_QUARTERLY)
  calendar/weekly/day=1     → 주간 전부 — 월요일 대신 주 마지막 거래일       (RB_WEEKLY)
  threshold/daily           → 주간 평가일마다 판정. 직전 리밸런싱 목표 비중이 가격으로 흘러간 추정
                              비중(runner.drifted_weights)에서 어느 한 종목이라도 |이탈| ≥ 5%p 면
                              리밸런싱 (RB_THRESHOLD_5). 일별 판정을 주간으로 근사한다
  signal/daily              → 주간 평가일마다 3관점 판단을 돌려 종목별 통합신호 방향(|s| < 0.10 은 0,
                              그 밖은 부호)이 직전 리밸런싱 때와 하나라도 다르면 리밸런싱 (RB_SIGNAL).
                              관점 가중치 채점 · 갱신은 리밸런싱일에만 한다(bridge.preview_rebalance)
min_interval_days 는 앞 리밸런싱일과의 간격이 모자란 날짜를 건너뛰는 것으로 적용한다(FN-506 최소
간격 검사). 달력형은 여기서 미리 거르고, threshold · signal 은 일정이 비중 · 신호에 달려 있어 러너가
판정 중에 거른다(runner.run 의 drift · signal_interval). 러너의 주간 구분이 해를 넘는 주를 둘로
나눠서 주간 규칙은 연말에 3~4일 간격이 생긴다. 15일·분기 규칙은 구간 첫 평가일이 리밸런싱일이
아닐 수 있다 — 첫 리밸런싱 전까지는 현금으로 있다.

검증 결과(확정 범위 · 현금 목표 · 하드캡으로 올린 간격 · 1회 손실)를 어디서 얻을지는 여기가
아니라 app/backtest/m2_chain.resolve_policy 가 정한다. 여기서는 그 간격(min_interval_days)을
받아 일정에 쓰고, 실행 기록에 남길 spec_universe 확정 범위(final_ranges)를 같이 읽어 줄 뿐이다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from app.repositories import etf_master, price_daily, specs

from .runner import (
    DRIFT_THRESHOLD,
    monthly_first,
    monthly_on_or_after,
    quarterly_first,
    weekly_dates,
)

# scripts/run_backtest_vbt.py 의 MARKET_TICKER 와 같아야 한다. 시장 종목은 평가 날짜
# 격자와 매수 후 보유 비교에 쓰이므로 유니버스에 없어도 읽는다.
# 기간 기본값(데모 계획 1.4)은 service.DEFAULT_PERIOD_START 한 곳에 둔다.
MARKET_TICKER = "069500"

# (type, freq, day) → (주간 평가일 → 후보일 함수, 화면에 적을 근사 설명, 판정 방식)
# 판정 방식: None = 달력(후보일이 곧 리밸런싱일) · float = 비중 이탈 임계값 · SIGNAL = 신호 방향 변화
# frontend/lib/backtest.ts 의 SCHEDULES 와 같아야 한다(두 러너 대조 tests/test_backtest_parity.py).
_SCHEDULES = {
    ("calendar", "monthly", 1): (
        monthly_first,
        "매달 첫 거래일 → 그 달 첫 주간 평가일(주 마지막 거래일)로 근사",
        None,
    ),
    ("calendar", "monthly", 15): (
        lambda dates: monthly_on_or_after(dates, 15),
        "매달 15일 근처 → 그 달 15일 이후 첫 주간 평가일(주 마지막 거래일)로 근사",
        None,
    ),
    ("calendar", "quarterly", 1): (
        quarterly_first,
        "분기 첫 거래일 → 그 분기(1·4·7·10월) 첫 주간 평가일(주 마지막 거래일)로 근사",
        None,
    ),
    ("calendar", "weekly", 1): (list, "매주 월요일 → 각 주 마지막 거래일로 근사", None),
    ("threshold", "daily", None): (
        list,
        "목표 비중에서 5%p 이상 벌어진 종목이 생기면 → 주간 평가일마다 흘러간 비중을 추정해 판정",
        DRIFT_THRESHOLD,
    ),
    ("signal", "daily", None): (
        list,
        "판단 계층의 신호 방향이 바뀌면 → 주간 평가일마다 종목별 통합신호 방향을 직전 리밸런싱과 비교",
        "signal",
    ),
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
    # 최소 간격에 걸려 건너뛴 리밸런싱 후보일(달력형). threshold 는 러너가 실행 중에 정한다
    skipped_rebalance_dates: list[str] = field(default_factory=list)
    # threshold 규칙이면 (임계값, 최소 간격 일수) — runner.run(drift=...) 에 그대로 넘긴다.
    # 그때 rebalance_dates 는 리밸런싱일이 아니라 판정 후보일(주간 평가일 전부)이다
    drift: tuple[float, int] | None = None
    # signal 규칙이면 최소 간격 일수 — runner.run(signal_interval=...). rebalance_dates 는 판정 후보일이다
    signal_interval: int | None = None
    # spec_universe 의 확정 범위 {종목: (하한, 상한)}. 검증 전에는 _raw 와 같다(M1 이 둘을 같이 쓴다).
    # holdings 는 데모 스크립트와 같은 모양으로 두려고 여기 따로 둔다
    final_ranges: dict[str, tuple[float, float]] = field(default_factory=dict)

    @property
    def data_snapshot_asof(self) -> str:
        # 실제로 쓴 가장 늦은 거래일
        return self.valuation_dates[-1]


def build_inputs(
    spec_id: str, *, period_start: date, period_end: date, min_interval_days: int | None = None
) -> BacktestInputs:
    """min_interval_days 를 주면 전략서 요청값 대신 그 값으로 간격을 건너뛴다(Validator 확정값).
    None 이면 요청값 그대로다."""
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
    _schedule(rule)

    holdings, final_ranges = _holdings(spec_id)
    tickers = [h["ticker"] for h in holdings]
    prices_wide = _prices_wide(sorted({*tickers, MARKET_TICKER}), as_of=period_end)

    start = period_start.isoformat()
    all_dates = [d for d in prices_wide[MARKET_TICKER].dropna().index if d >= start]
    if not all_dates:
        raise InputError(f"기간 안에 거래일이 없다: {period_start} ~ {period_end}")
    weekly = weekly_dates(all_dates)
    plan = plan_schedule(rule, weekly, min_interval_days=min_interval_days)

    return BacktestInputs(
        spec_id=spec_id,
        risk_level=risk_level,
        holdings=holdings,
        prices_wide=prices_wide,
        valuation_dates=weekly,
        rebalance_dates=plan.dates,
        rebalance_rule=rule,
        schedule_note=plan.note,
        skipped_rebalance_dates=plan.skipped,
        drift=plan.drift,
        signal_interval=plan.signal_interval,
        final_ranges=final_ranges,
    )


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


@dataclass(frozen=True)
class Schedule:
    dates: list[str]  # 달력형은 리밸런싱일, threshold · signal 은 판정 후보일
    skipped: list[str]  # 최소 간격에 걸린 날(달력형만 — 나머지는 러너가 정한다)
    note: str
    drift: tuple[float, int] | None = None  # (임계값, 최소 간격)
    signal_interval: int | None = None  # 신호형 최소 간격


def plan_schedule(rule: dict | None, weekly: list[str], *, min_interval_days: int | None = None) -> Schedule:
    """리밸런싱 규칙 + 주간 평가일 → Schedule.

    DB 를 보지 않는 순수 함수다 — TS 러너의 scheduleFor 와 대조한다(tests/test_backtest_parity.py).
    min_interval_days 를 주면 규칙의 요청값 대신 쓴다(Validator 확정값).
    """
    schedule, note, mode = _schedule(rule)
    interval = rule["min_interval_days"] if min_interval_days is None else min_interval_days
    if mode is None:
        rebalance, skipped = _apply_min_interval(schedule(weekly), interval)
        return Schedule(rebalance, skipped, note)
    if mode == "signal":
        return Schedule(schedule(weekly), [], note, signal_interval=interval)
    return Schedule(schedule(weekly), [], note, drift=(mode, interval))


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
