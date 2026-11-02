# 자산 배분형 전략서(v0.2, rules 가 빈 것) 백테스트 엔진. DB 를 모르는 순수 함수이고 stdlib 만 쓴다.
#
# 설명을 docstring 이 아니라 주석에 두는 이유는 features.py 와 같다(as_of 가드 사정권).
# 조건형 엔진(rules_engine.py)과 비용 · 세금 · 1주 단위 · 결과 모양이 같다(docs/v0.2-plan.md 9단계).
#
# ── 규칙 ─────────────────────────────────────────────────────────────
# 1. 첫 거래일 시가에 목표 비중대로 산다. 목표 비중은 weights(종목별) 또는 1/N, 종목당 최대 비중으로 자른다.
#    합이 1 보다 작으면 나머지는 현금이다
# 2. 리밸런싱 날(eval/GUIDE.md 의 블록 다섯)에는 그날 시가에, 전날 종가 기준 평가액으로
#    목표 수량을 다시 맞춘다.
#    전날 종가로 정하는 것은 그날 종가를 미리 보지 않기 위해서다
#      RB_MONTHLY_FIRST 매달 첫 거래일 · RB_MONTHLY_MID 매달 15일 이후 첫 거래일
#      RB_QUARTERLY 1 · 4 · 7 · 10월 첫 거래일 · RB_WEEKLY 매주 마지막 거래일
#      RB_THRESHOLD_5 종가 비중이 목표에서 5%p 이상 벌어진 종목이 있으면 다음 날 시가에
#    리밸런싱이 없으면(None) 처음에 사서 들고 있는다
# 3. 목표 수량 = 목표 금액 / (시가 x 체결 오차 x 수수료) 의 소수점 버림.
#    팔기를 먼저 체결하고 사기는 종목코드 순, 가진 현금까지만 산다
# 4. 거래정지일(시가가 빈 날)에는 그 종목만 이번 리밸런싱을 건너뛴다(unfilled 에 남긴다)
# 5. 일부만 팔 수 있으므로 그 밖의 ETF 차익은 평균 매수 단가로 계산한다(taxes.py)
# 6. 체결마다 목표 비중(target_weight)을 남긴다. 평가가 주문을 비교하는 기준이다(eval/README.md)
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date

from app.backtest import taxes
from app.backtest.rules_engine import (
    FEE_RATE,
    SLIPPAGE_RATE,
    RulesResult,
    UnsupportedStrategy,
    _Bars,
    slot_fractions,
)
from app.contracts.strategy_v0_2 import StrategyV0_2

DRIFT_THRESHOLD = 0.05


def run_allocation(
    spec: StrategyV0_2,
    bars: Mapping[str, Sequence[Mapping]],
    calendar: Sequence[date],
    categories: Mapping[str, str],
) -> RulesResult:
    tickers = spec.universe.held_tickers()
    if tickers is None:
        raise UnsupportedStrategy("종류로 고른 종목(selector)을 아직 풀지 않았다")
    if spec.is_rules_strategy:
        raise UnsupportedStrategy("rules 가 있는 전략서는 조건형 엔진(rules_engine)이 돌린다")
    if not calendar:
        raise ValueError("기간 안에 거래일이 없다")
    tickers = sorted(tickers)
    series = {t: _Bars(bars[t]) for t in tickers}
    target = slot_fractions(spec, tickers)
    scheduled = set(rebalance_days(spec.rebalance, list(calendar)))

    cash = float(spec.backtest.capital)
    shares = {t: 0 for t in tickers}
    avg_cost = {t: 0.0 for t in tickers}
    result = RulesResult(equity=[], cash=[], fills=[], trades=[], unfilled=[], same_day_conflicts=[])
    prev_equity = cash
    drift_next = False

    for i, day in enumerate(calendar):
        if i == 0 or day in scheduled or drift_next:
            cash = _rebalance(
                day, prev_equity, target, tickers, series, categories, shares, avg_cost, cash, result
            )
            drift_next = False
        values = {t: shares[t] * (series[t].close_on_or_before(day) or 0.0) for t in tickers}
        equity = cash + math.fsum(values.values())
        result.equity.append((day, equity))
        result.cash.append((day, cash))
        prev_equity = equity
        if spec.rebalance == "RB_THRESHOLD_5" and equity > 0 and i < len(calendar) - 1:
            drift_next = any(abs(values[t] / equity - target[t]) >= DRIFT_THRESHOLD - 1e-12 for t in tickers)

    result.holdings_at_end = {t: n for t, n in shares.items() if n > 0}
    return result


def _rebalance(day, equity, target, tickers, series, categories, shares, avg_cost, cash, result) -> float:
    plan: dict[str, tuple[int, float]] = {}
    for t in tickers:
        k = series[t].index.get(day)
        open_price = series[t].opens[k] if k is not None else None
        if open_price is None or open_price <= 0:
            if target[t] > 0 or shares[t] > 0:
                result.unfilled.append(
                    {
                        "ticker": t,
                        "side": "rebalance",
                        "decided_on": day,
                        "reason": "거래정지일이거나 가격이 없어 이번 리밸런싱을 건너뜀",
                    }
                )
            continue
        unit_cost = open_price * (1 + SLIPPAGE_RATE) * (1 + FEE_RATE)
        plan[t] = (math.floor(equity * target[t] / unit_cost), open_price)

    for t in sorted(plan):  # 팔기 먼저
        want, open_price = plan[t]
        if shares[t] <= want:
            continue
        n = shares[t] - want
        price = open_price * (1 - SLIPPAGE_RATE)
        proceeds = n * price
        fee = proceeds * FEE_RATE
        tax = taxes.sell_tax(categories[t], trade_date=day, proceeds=proceeds, gain=(price - avg_cost[t]) * n)
        cash += proceeds - fee - tax
        shares[t] -= n
        result.fills.append(
            {
                "date": day,
                "ticker": t,
                "side": "sell",
                "shares": n,
                "price": price,
                "fee": fee,
                "tax": tax,
                "decided_on": day,
                "cash_after": cash,
                "target_weight": target[t],
            }
        )
    for t in sorted(plan):  # 그다음 사기
        want, open_price = plan[t]
        if shares[t] >= want:
            continue
        price = open_price * (1 + SLIPPAGE_RATE)
        unit_cost = price * (1 + FEE_RATE)
        n = min(want - shares[t], math.floor(cash / unit_cost))
        if n <= 0:
            result.unfilled.append(
                {"ticker": t, "side": "buy", "decided_on": day, "reason": "현금이 1주 값보다 작음"}
            )
            continue
        fee = n * price * FEE_RATE
        cash -= n * price + fee
        avg_cost[t] = (avg_cost[t] * shares[t] + price * n) / (shares[t] + n)
        shares[t] += n
        result.fills.append(
            {
                "date": day,
                "ticker": t,
                "side": "buy",
                "shares": n,
                "price": price,
                "fee": fee,
                "tax": 0.0,
                "decided_on": day,
                "cash_after": cash,
                "target_weight": target[t],
            }
        )
    return cash


# ── 리밸런싱 날 (eval/GUIDE.md "전략 유형") ──────────────────────────


def rebalance_days(rebalance: str | None, calendar: list[date]) -> list[date]:
    if rebalance in (None, "RB_THRESHOLD_5"):
        return []
    if rebalance == "RB_MONTHLY_FIRST":
        return _first_of_each(calendar, lambda d: (d.year, d.month))
    if rebalance == "RB_MONTHLY_MID":
        return _first_of_each([d for d in calendar if d.day >= 15], lambda d: (d.year, d.month))
    if rebalance == "RB_QUARTERLY":
        return _first_of_each(calendar, lambda d: (d.year, (d.month - 1) // 3))
    if rebalance == "RB_WEEKLY":
        last: dict[tuple, date] = {}
        for d in calendar:
            last[d.isocalendar()[:2]] = d
        return sorted(last.values())
    raise UnsupportedStrategy(f"모르는 리밸런싱: {rebalance}")


def _first_of_each(days: list[date], key) -> list[date]:
    out, seen = [], set()
    for d in days:
        k = key(d)
        if k not in seen:
            seen.add(k)
            out.append(d)
    return out
