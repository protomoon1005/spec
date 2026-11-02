"""비교 대상의 주문을 우리 엔진과 같은 규칙으로 체결하고, 정답 체결과 비교한다.

demo/baseline/orders.py 의 execute_orders 를 옮겼다(docs/v0.2-plan.md 9단계).
주문 = {"date": 판단한 거래일, "ticker", "side": buy · sell, "weight": 체결 뒤 목표 비중(0~1)}

체결(우리 엔진과 같은 비용 · 세금, app/backtest/rules_engine.py · taxes.py)
  - 판단일 다음 거래일 시가. 매수는 시가 x (1 + 체결 오차), 매도는 시가 x (1 - 체결 오차), 수수료는 각각
  - 1주 단위. 그날 시가 기준 평가액 x 목표 비중이 되도록 넘치는 종목을 먼저 팔고,
    모자라는 종목을 남은 현금으로 산다
  - 같은 종목에 체결 전 주문이 여럿 쌓이면 마지막(가장 늦게 판단한) 주문만 체결한다
  - 체결일이 기간 밖이면 체결하지 않는다
비교(eval/README.md "주문이 같다"): 체결일 · 종목 · 방향이 같고 목표 비중 차이가 0.5%p 이내
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date

import pandas as pd

from app.backtest import taxes
from app.backtest.rules_engine import FEE_RATE, SLIPPAGE_RATE

WEIGHT_TOLERANCE = 0.005
_EPS = 1e-9


def execute_orders(
    orders: list[dict],
    prices: Mapping[str, pd.DataFrame],
    start: date,
    end: date,
    capital: float,
    categories: Mapping[str, str],
) -> list[dict]:
    tickers = sorted({o["ticker"] for o in orders})
    by_day: dict[pd.Timestamp, dict[str, dict]] = {}
    for o in sorted(orders, key=lambda x: x["date"]):
        idx = prices[o["ticker"]].index
        k = int(idx.searchsorted(pd.Timestamp(o["date"]), side="right"))
        if k < len(idx) and pd.Timestamp(start) <= idx[k] <= pd.Timestamp(end):
            by_day.setdefault(idx[k], {})[o["ticker"]] = o
    cash, shares, avg = float(capital), dict.fromkeys(tickers, 0), dict.fromkeys(tickers, 0.0)
    fills: list[dict] = []
    for day in sorted(by_day):
        todo = by_day[day]
        price_now = {t: _open(prices[t], day) or _close_before(prices[t], day) for t in tickers}
        equity = cash + sum(shares[t] * price_now[t] for t in tickers if shares[t] and price_now[t])
        for t, o in sorted(todo.items()):  # 넘치는 쪽 먼저 판다
            op = _open(prices[t], day)
            if not op:
                continue
            excess = shares[t] * op - equity * o["weight"]
            if excess > 0:
                n = shares[t] if o["weight"] <= _EPS else min(shares[t], math.floor(excess / op + _EPS))
                if n > 0:
                    px = op * (1 - SLIPPAGE_RATE)
                    proceeds = n * px
                    tax = taxes.sell_tax(
                        categories[t], trade_date=day.date(), proceeds=proceeds, gain=(px - avg[t]) * n
                    )
                    cash += proceeds - proceeds * FEE_RATE - tax
                    shares[t] -= n
                    fills.append(
                        {
                            "date": day.date(),
                            "ticker": t,
                            "side": "sell",
                            "shares": n,
                            "price": px,
                            "cash_after": cash,
                            "target_weight": o["weight"],
                        }
                    )
        for t, o in sorted(todo.items()):
            op = _open(prices[t], day)
            if not op:
                continue
            deficit = equity * o["weight"] - shares[t] * op
            if deficit > 0:
                px = op * (1 + SLIPPAGE_RATE)
                n = math.floor(min(deficit, cash) / (px * (1 + FEE_RATE)) + _EPS)
                if n > 0:
                    cash -= n * px * (1 + FEE_RATE)
                    avg[t] = (avg[t] * shares[t] + px * n) / (shares[t] + n)
                    shares[t] += n
                    fills.append(
                        {
                            "date": day.date(),
                            "ticker": t,
                            "side": "buy",
                            "shares": n,
                            "price": px,
                            "cash_after": cash,
                            "target_weight": o["weight"],
                        }
                    )
    return fills


def equity_curve(
    fills: list[dict], prices: Mapping[str, pd.DataFrame], calendar: list[date], capital: float
) -> list[tuple[date, float]]:
    # 체결 기록을 날짜 순으로 다시 따라가며 날마다 현금 + 보유 종목 종가 평가액을 낸다
    shares: dict[str, int] = {}
    cash = float(capital)
    ordered = sorted(fills, key=lambda f: f["date"])
    k = 0
    out = []
    for day in calendar:
        while k < len(ordered) and ordered[k]["date"] <= day:
            f = ordered[k]
            shares[f["ticker"]] = shares.get(f["ticker"], 0) + (
                f["shares"] if f["side"] == "buy" else -f["shares"]
            )
            cash = f["cash_after"]
            k += 1
        value = cash
        for t, n in shares.items():
            if n:
                value += n * (_close_before(prices[t], pd.Timestamp(day)) or 0.0)
        out.append((day, value))
    return out


def same_fills(a: list[dict], b: list[dict], tol: float = WEIGHT_TOLERANCE) -> bool:
    key = lambda f: (str(f["date"]), f["ticker"], f["side"])  # noqa: E731
    if sorted(map(key, a)) != sorted(map(key, b)):
        return False
    rest = sorted(b, key=key)
    for f in sorted(a, key=key):
        hit = next(
            (
                i
                for i, g in enumerate(rest)
                if key(g) == key(f) and abs(g["target_weight"] - f["target_weight"]) <= tol
            ),
            None,
        )
        if hit is None:
            return False
        rest.pop(hit)
    return True


def _open(frame: pd.DataFrame, day) -> float | None:
    if day not in frame.index:
        return None
    value = frame.at[day, "Open"]
    return (
        None
        if value is None or (isinstance(value, float) and math.isnan(value)) or value <= 0
        else float(value)
    )


def _close_before(frame: pd.DataFrame, day) -> float | None:
    before = frame.loc[:day, "Close"]
    return float(before.iloc[-1]) if len(before) else None
