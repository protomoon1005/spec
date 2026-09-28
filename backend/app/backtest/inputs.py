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
    ("calendar", "weekly", 1): (
        lambda weekly: list(weekly),
        "매주 월요일 → 각 주 마지막 거래일로 근사",
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
    # 최소 간격에 걸려 건너뛴 리밸런싱 후보일
    skipped_rebalance_dates: list[str] = field(default_factory=list)

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

    holdings = _holdings(spec_id)
    tickers = [h["ticker"] for h in holdings]
    prices_wide = _prices_wide(sorted({*tickers, MARKET_TICKER}), as_of=period_end)

    start = period_start.isoformat()
    all_dates = [d for d in prices_wide[MARKET_TICKER].dropna().index if d >= start]
    if not all_dates:
        raise InputError(f"기간 안에 거래일이 없다: {period_start} ~ {period_end}")
    weekly = weekly_dates(all_dates)
    rebalance, skipped = _apply_min_interval(schedule(weekly), rule["min_interval_days"])

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
    )


def _holdings(spec_id: str) -> list[dict]:
    # 키는 scripts/run_backtest_vbt.py 의 holdings 와 같다.
    universe = specs.get_spec_universe(spec_id)
    if not universe:
        raise InputError(f"전략서에 종목이 없다: {spec_id}")
    records = etf_master.get_by_tickers([u["ticker"] for u in universe])

    holdings = []
    for u in universe:
        rec = records[u["ticker"]]  # spec_universe.ticker 는 etf_master FK 라 항상 있다
        missing = [
            name
            for name, value in (
                ("risk_tag", rec.risk_tag),
                ("asset_group_id", rec.asset_group_id),
                ("sector_group_id", rec.sector_group_id),
                ("country_group_id", rec.country_group_id),
                ("weight_min_raw", u["weight_min_raw"]),
                ("weight_max_raw", u["weight_max_raw"]),
            )
            if value is None
        ]
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
    return holdings


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
    trigger = (rule or {}).get("trigger") or {}
    key = (trigger.get("type"), trigger.get("freq"), trigger.get("day"))
    if key not in _SCHEDULES or "min_interval_days" not in (rule or {}):
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
