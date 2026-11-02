# 조건형 전략서(v0.2) 백테스트 엔진. DB 를 모르는 순수 함수이고 stdlib 만 쓴다.
#
# 설명을 docstring 이 아니라 주석에 두는 이유는 features.py 와 같다(as_of 가드 사정권).
# 가격은 호출부(rules_service.py)가 저장소 함수로 as_of 를 지켜 읽어 넘긴다.
#
# ── 규칙 (설계 문서 "데모 범위와 조건형 백테스트", 2026-11-02) ─────────
# 1. t일 종가까지로 판단하고 t+1일 시가에 체결한다
# 2. 자리 크기 = t일 종가 기준 평가액(현금 + 보유 평가액) x 자리 비율. 자리 비율은 weights 가
#    종목별이면 그 값, equal 이면 1/N. 종목당 최대 비중이 있으면 그보다 크지 않다.
#    가진 현금보다 많이는 사지 않는다. 수량은 소수점을 버린다(1주 단위). 남는 돈은 현금
# 3. 한 번 사면 팔 때까지 더 사지 않고, 팔 때는 전부 판다. 그래서 종목마다 매수 단가가 하나다
# 4. 같은 날 주문은 팔기를 먼저 체결하고, 판 돈을 같은 날 살 때 쓴다. 사기는 종목코드 순이다
# 5. 같은 날 사는 조건과 파는 조건이 함께 참이면 팔기가 우선이다. 들고 있지 않으면 사지 않는다
# 6. 비용: 체결 오차 0.05%(살 때 시가 x 1.0005, 팔 때 시가 x 0.9995), 수수료 0.015%(사고팔 때 모두)
# 7. 거래정지일(시가가 빈 날)에는 체결하지 않고 다음 거래 가능일 시가로 미룬다.
#    한 번 사면 팔 때까지 더 사지 않으므로, 미루는 동안 반대 주문은 생길 수 없다
#    (들고 있지 않으면 팔 조건을 보지 않고, 들고 있으면 살 조건을 보지 않는다)
# 8. 기간 끝까지 들고 있는 종목은 팔지 않고 마지막 종가로 평가만 한다. 그래서 세금도 없다.
#    마지막 날 나온 신호는 다음 날이 기간 밖이라 체결하지 않는다(unfilled 에 남긴다)
#
# ── 판정 세부 ────────────────────────────────────────────────────────
# - 판단은 그 종목의 행이 있는 날에만 한다. 상장 전이거나 행이 없는 날은 건너뛴다
# - 다른 종목을 보는 조건(of = 종목코드)은 그 종목의 그날 이전 마지막 값을 쓴다(과거 방향으로만)
# - "~하는 순간"(cross)은 그 종목의 직전 행 날짜와 그날의 값을 비교한다.
#   떨어지는 순간: 전날 왼쪽 >= 오른쪽 이고 오늘 왼쪽 < 오른쪽. 올라가는 순간은 반대
# - 값이 없으면(이력 부족) 그 비교는 거짓이다
# - holding_days: 체결일을 0일로 센 그 종목의 거래일 수. entry_return: 종가 / 매수 체결가 - 1(소수)
# - 지수 · VIX 를 보는 조건은 데모 범위 밖이라 오류로 멈춘다
from __future__ import annotations

import bisect
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from app.backtest import signals, taxes
from app.contracts.strategy_v0_2 import AllOf, Compare, Cond, Indicator, Rule, StrategyV0_2, Value

FEE_RATE = 0.00015
SLIPPAGE_RATE = 0.0005
UNSUPPORTED_SUBJECTS = ("KOSPI200", "KOSDAQ", "VIX")
POSITION_INDICATORS = ("entry_return", "holding_days")


class UnsupportedStrategy(ValueError):
    pass


@dataclass
class _Position:
    shares: int
    buy_price: float  # 체결 오차를 넣은 매수 체결가(수수료 제외)
    fill_date: date
    fill_index: int  # 그 종목 행 번호
    buy_fee: float
    decided_on: date


@dataclass
class _Order:
    side: str  # buy · sell
    decided_on: date
    amount: float = 0.0  # buy 일 때 자리 금액


@dataclass
class RulesResult:
    equity: list[tuple[date, float]]
    cash: list[tuple[date, float]]
    fills: list[dict]
    trades: list[dict]
    unfilled: list[dict]
    same_day_conflicts: list[dict]
    holdings_at_end: dict[str, int] = field(default_factory=dict)


class _Bars:
    # 한 종목의 일봉. 날짜 순이고 as_of(기간 끝) 이하만 들어온다
    def __init__(self, rows: Sequence[Mapping]):
        self.dates: list[date] = [r["trade_date"] for r in rows]
        self.opens: list[float | None] = [r.get("open") for r in rows]
        self.closes: list[float] = [float(r["close"]) for r in rows]
        self.volumes: list[float] = [float(r.get("volume") or 0.0) for r in rows]
        self.index = {d: k for k, d in enumerate(self.dates)}
        self._series: dict[tuple[str, int | None], list[float | None]] = {}

    def series(self, indicator: str, window: int | None) -> list[float | None]:
        key = (indicator, window)
        if key not in self._series:
            self._series[key] = signals.indicator_series(self.closes, self.volumes, indicator, window)
        return self._series[key]

    def last_index_on_or_before(self, day: date) -> int | None:
        k = bisect.bisect_right(self.dates, day) - 1
        return k if k >= 0 else None

    def close_on_or_before(self, day: date) -> float | None:
        k = self.last_index_on_or_before(day)
        return None if k is None else self.closes[k]


def run_rules(
    spec: StrategyV0_2,
    bars: Mapping[str, Sequence[Mapping]],
    calendar: Sequence[date],
    categories: Mapping[str, str],
) -> RulesResult:
    tickers = spec.universe.held_tickers()
    if tickers is None:
        raise UnsupportedStrategy("종류로 고른 종목(selector)을 아직 풀지 않았다")
    if not spec.is_rules_strategy:
        raise UnsupportedStrategy("rules 가 없는 자산 배분형은 이 엔진이 돌리지 않는다")
    if not calendar:
        raise ValueError("기간 안에 거래일이 없다")
    _check_subjects(spec)

    tickers = sorted(tickers)
    series = {t: _Bars(rows) for t, rows in bars.items()}
    missing = [t for t in tickers if t not in series]
    if missing:
        raise ValueError(f"가격이 없는 종목: {missing}")
    rule_of = _rules_by_ticker(spec.rules, tickers)
    slot = slot_fractions(spec, tickers)

    cash = float(spec.backtest.capital)
    positions: dict[str, _Position] = {}
    pending: dict[str, _Order] = {}
    result = RulesResult(equity=[], cash=[], fills=[], trades=[], unfilled=[], same_day_conflicts=[])

    for day_index, day in enumerate(calendar):
        cash = _fill_pending(day, pending, positions, series, categories, cash, result)

        equity = cash + math.fsum(
            pos.shares * (series[t].close_on_or_before(day) or 0.0) for t, pos in positions.items()
        )
        result.equity.append((day, equity))
        result.cash.append((day, cash))

        last_day = day_index == len(calendar) - 1
        for ticker in tickers:
            rule = rule_of.get(ticker)
            if rule is None or ticker in pending or day not in series[ticker].index:
                continue
            order = _decide(ticker, rule, day, positions.get(ticker), series, result)
            if order is None:
                continue
            if order == "buy":
                pending[ticker] = _Order("buy", day, amount=equity * slot[ticker])
            else:
                pending[ticker] = _Order("sell", day)
        if last_day:
            for ticker, order in sorted(pending.items()):
                result.unfilled.append({
                    "ticker": ticker, "side": order.side, "decided_on": order.decided_on,
                    "reason": "기간이 끝나 체결하지 않음",
                })
            pending.clear()

    result.holdings_at_end = {t: p.shares for t, p in sorted(positions.items())}
    return result


def _check_subjects(spec: StrategyV0_2) -> None:
    for rule in spec.rules:
        for cond in (rule.entry, rule.exit):
            for operand in operands(cond):
                if operand.of in UNSUPPORTED_SUBJECTS:
                    raise UnsupportedStrategy(f"{operand.of} 를 보는 조건은 데모 범위 밖이다")
                if operand.indicator == "vix":
                    raise UnsupportedStrategy("VIX 조건은 데모 범위 밖이다")
                if operand.indicator in POSITION_INDICATORS and operand.of != "self":
                    raise UnsupportedStrategy(f"{operand.indicator} 는 그 종목 자신에만 쓴다")


def operands(cond: Cond | None) -> list[Indicator]:
    if cond is None:
        return []
    compares = [cond] if isinstance(cond, Compare) else (cond.all if isinstance(cond, AllOf) else cond.any)
    out: list[Indicator] = []
    for c in compares:
        out.append(c.left)
        if isinstance(c.right, Indicator):
            out.append(c.right)
    return out


def _rules_by_ticker(rules: Sequence[Rule], tickers: Sequence[str]) -> dict[str, Rule]:
    # 종목코드를 지목한 rule 이 each 보다 먼저다. 한 종목에 같은 급의 rule 이 둘이면 막는다
    specific: dict[str, Rule] = {}
    each: list[Rule] = []
    for rule in rules:
        if rule.target == "each":
            each.append(rule)
        elif rule.target in specific:
            raise UnsupportedStrategy(f"{rule.target} 에 rule 이 둘이다")
        else:
            specific[rule.target] = rule
    if len(each) > 1:
        raise UnsupportedStrategy("target each 인 rule 이 둘이다")
    out: dict[str, Rule] = {}
    for ticker in tickers:
        if ticker in specific:
            out[ticker] = specific[ticker]
        elif each:
            out[ticker] = each[0]
    return out


def slot_fractions(spec: StrategyV0_2, tickers: Sequence[str]) -> dict[str, float]:
    if isinstance(spec.weights, dict):
        base = {t: spec.weights.get(t, 0.0) for t in tickers}
    else:
        base = {t: 1.0 / len(tickers) for t in tickers}
    cap = spec.constraint.max_weight_per_asset
    return {t: (min(w, cap) if cap is not None else w) for t, w in base.items()}


def _fill_pending(day, pending, positions, series, categories, cash, result) -> float:
    if not pending:
        return cash
    for side in ("sell", "buy"):
        for ticker in sorted(t for t, o in pending.items() if o.side == side):
            bars = series[ticker]
            k = bars.index.get(day)
            open_price = bars.opens[k] if k is not None else None
            if open_price is None or open_price <= 0:
                continue  # 거래정지일이거나 행이 없다. 다음 거래 가능일로 미룬다
            order = pending.pop(ticker)
            if side == "sell":
                cash = _sell(ticker, day, k, open_price, order, positions, categories, cash, result)
            else:
                cash = _buy(ticker, day, k, open_price, order, positions, cash, result)
    return cash


def _buy(ticker, day, k, open_price, order, positions, cash, result) -> float:
    price = open_price * (1 + SLIPPAGE_RATE)
    unit_cost = price * (1 + FEE_RATE)
    budget = min(order.amount, cash)
    shares = math.floor(budget / unit_cost) if unit_cost > 0 else 0
    if shares <= 0:
        result.unfilled.append({
            "ticker": ticker, "side": "buy", "decided_on": order.decided_on,
            "reason": "자리 금액이나 현금이 1주 값보다 작음",
        })
        return cash
    fee = shares * price * FEE_RATE
    cash -= shares * price + fee
    positions[ticker] = _Position(shares, price, day, k, fee, order.decided_on)
    result.fills.append({
        "date": day, "ticker": ticker, "side": "buy", "shares": shares, "price": price,
        "fee": fee, "tax": 0.0, "decided_on": order.decided_on, "cash_after": cash,
    })
    return cash


def _sell(ticker, day, k, open_price, order, positions, categories, cash, result) -> float:
    pos = positions.pop(ticker)
    price = open_price * (1 - SLIPPAGE_RATE)
    proceeds = pos.shares * price
    fee = proceeds * FEE_RATE
    tax = taxes.sell_tax(
        categories[ticker], trade_date=day, proceeds=proceeds, gain=(price - pos.buy_price) * pos.shares
    )
    cash += proceeds - fee - tax
    result.fills.append({
        "date": day, "ticker": ticker, "side": "sell", "shares": pos.shares, "price": price,
        "fee": fee, "tax": tax, "decided_on": order.decided_on, "cash_after": cash,
    })
    cost = pos.shares * pos.buy_price + pos.buy_fee
    pnl = proceeds - fee - tax - cost
    result.trades.append({
        "ticker": ticker, "shares": pos.shares,
        "buy_date": pos.fill_date, "buy_price": pos.buy_price,
        "sell_date": day, "sell_price": price,
        "holding_days": k - pos.fill_index, "pnl": pnl, "return": pnl / cost,
    })
    return cash


def _decide(ticker, rule, day, position, series, result) -> str | None:
    bars = series[ticker]
    k = bars.index[day]
    prev_day = bars.dates[k - 1] if k > 0 else None
    if position is not None:
        if rule.exit is not None and _eval(rule.exit, ticker, day, prev_day, position, series):
            return "sell"
        return None
    if rule.entry is None or not _eval(rule.entry, ticker, day, prev_day, None, series):
        return None
    if rule.exit is not None and _eval(rule.exit, ticker, day, prev_day, None, series):
        result.same_day_conflicts.append({"date": day, "ticker": ticker})
        return None
    return "buy"


def _eval(cond, ticker, day, prev_day, position, series) -> bool:
    if isinstance(cond, Compare):
        return _compare(cond, ticker, day, prev_day, position, series)
    if isinstance(cond, AllOf):
        return all(_compare(c, ticker, day, prev_day, position, series) for c in cond.all)
    return any(_compare(c, ticker, day, prev_day, position, series) for c in cond.any)


def _compare(c: Compare, ticker, day, prev_day, position, series) -> bool:
    left = _value(c.left, ticker, day, position, series)
    right = _value(c.right, ticker, day, position, series)
    if left is None or right is None:
        return False
    if c.op == "<":
        return left < right
    if c.op == "<=":
        return left <= right
    if c.op == ">":
        return left > right
    if c.op == ">=":
        return left >= right
    if prev_day is None:
        return False
    prev_left = _value(c.left, ticker, prev_day, position, series)
    prev_right = _value(c.right, ticker, prev_day, position, series)
    if prev_left is None or prev_right is None:
        return False
    if c.op == "cross_below":
        return prev_left >= prev_right and left < right
    return prev_left <= prev_right and left > right  # cross_above


def _value(operand, ticker, day, position, series) -> float | None:
    if isinstance(operand, Value):
        return operand.value
    if operand.indicator in POSITION_INDICATORS:
        return _position_value(operand.indicator, ticker, day, position, series)
    subject = ticker if operand.of == "self" else operand.of
    bars = series.get(subject)
    if bars is None:
        raise ValueError(f"가격이 없는 종목을 보는 조건: {subject}")
    k = bars.last_index_on_or_before(day)
    if k is None:
        return None
    return bars.series(operand.indicator, operand.window)[k]


def _position_value(indicator, ticker, day, position, series) -> float | None:
    if position is None or day < position.fill_date:
        return None
    bars = series[ticker]
    k = bars.last_index_on_or_before(day)
    if indicator == "holding_days":
        return float(k - position.fill_index)
    return bars.closes[k] / position.buy_price - 1.0


# ── 비교 기준 (설계 문서 규칙 7) ─────────────────────────────────────
# 시작일에 자리 비율만큼 사서 끝까지 들고 있기. 1주 단위, 같은 비용. 팔지 않으므로 세금은 없다.
# 시작일 시가가 없으면(거래정지 · 상장 전) 첫 거래 가능일에 산다.


def run_buy_and_hold(
    fractions: Mapping[str, float],
    bars: Mapping[str, Sequence[Mapping]],
    calendar: Sequence[date],
    capital: float,
) -> list[tuple[date, float]]:
    series = {t: _Bars(bars[t]) for t in fractions}
    cash = float(capital)
    shares: dict[str, int] = {}
    budget = {t: capital * f for t, f in fractions.items()}
    out: list[tuple[date, float]] = []
    for day in calendar:
        for ticker in sorted(fractions):
            if ticker in shares:
                continue
            k = series[ticker].index.get(day)
            open_price = series[ticker].opens[k] if k is not None else None
            if open_price is None or open_price <= 0:
                continue
            price = open_price * (1 + SLIPPAGE_RATE)
            unit_cost = price * (1 + FEE_RATE)
            n = math.floor(min(budget[ticker], cash) / unit_cost)
            shares[ticker] = n
            cash -= n * unit_cost
        value = cash + math.fsum(
            n * (series[t].close_on_or_before(day) or 0.0) for t, n in shares.items()
        )
        out.append((day, value))
    return out


def summarize(equity: Sequence[tuple[date, float]]) -> dict:
    # 일별 평가액으로 수익률 · 연환산 수익률 · 최대 낙폭 · 연환산 변동성을 낸다
    values = [v for _, v in equity]
    first, last = values[0], values[-1]
    days = (equity[-1][0] - equity[0][0]).days
    years = days / 365.25 if days > 0 else 0.0
    peak, mdd = first, 0.0
    for v in values:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1.0)
    rets = [b / a - 1.0 for a, b in zip(values, values[1:], strict=False) if a > 0]
    vol = 0.0
    if len(rets) > 1:
        mean = math.fsum(rets) / len(rets)
        vol = math.sqrt(math.fsum((r - mean) ** 2 for r in rets) / (len(rets) - 1)) * math.sqrt(252)
    return {
        "total_return": last / first - 1.0,
        "cagr": (last / first) ** (1 / years) - 1.0 if years > 0 and last > 0 else None,
        "mdd": mdd,
        "volatility": vol,
        "final_value": last,
    }
