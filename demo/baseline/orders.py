"""주문 목록: 같은 형식으로 맞추고, 비교하고, 체결해 자산 곡선을 만든다. LLM·random 미사용.

주문 = {"date": 판단한 거래일, "ticker", "side": buy|sell, "weight": 체결 뒤 목표 비중(0~1)}
  우리 엔진처럼 date 종가로 판단 → 그 종목의 다음 거래일 시가에 체결.

"주문이 같다" (docs/strategy-spec-v0.2.md): 거래일 · 종목 · 방향이 같고 목표 비중 차이가 0.5%p 이내.

baseline 주문의 체결 (execute_orders) — 비교를 위해 우리 엔진과 같은 비용·세금을 쓴다
  - 체결가: 매수 시가 × (1 + 체결 오차), 매도 시가 × (1 - 체결 오차). 수수료는 매수·매도 금액에 각각.
  - 세금: core.tax (매도 체결일 세율, 그 밖의 ETF 차익 15.4%).
  - 1주 단위. 그날 시가 기준 총자산 × 목표 비중이 되도록: 넘치는 종목을 먼저 팔고, 모자라는 종목을 남은 현금 안에서 산다.
    side 는 비교용이고 체결은 weight 를 따른다.
  - 같은 종목에 체결 전 주문이 여럿 쌓이면 마지막(가장 늦게 판단한) 주문만 체결한다.
  - 체결일이 기간 첫 거래일보다 앞이면 버리고(기간 밖), 기간 마지막 날 이후면 미체결로 남긴다.
  - 기간 끝 보유분은 마지막 종가로 평가만 한다 (우리 엔진과 같음).
"""
from __future__ import annotations

import math
from typing import Optional

import pandas as pd

from core.dictionary import tax_category
from core.tax import gain_tax_rate, sell_tax_rate

WEIGHT_TOL = 0.005
_EPS = 1e-9


def _key(o: dict) -> tuple:
    return (o["date"], o["ticker"], o["side"])


def same_orders(a: list[dict], b: list[dict], tol: float = WEIGHT_TOL) -> bool:
    """두 주문 목록이 하나하나 같은가 (순서 무관)."""
    if len(a) != len(b):
        return False
    rest = sorted(b, key=_key)
    for o in sorted(a, key=_key):
        hit = next((k for k, x in enumerate(rest) if _key(x) == _key(o) and abs(x["weight"] - o["weight"]) <= tol), None)
        if hit is None:
            return False
        rest.pop(hit)
    return True


def match_counts(pred: list[dict], gold: list[dict], tol: float = WEIGHT_TOL) -> dict:
    """주문 단위 일치: {"matched", "pred", "gold"}. 하나의 정답 주문에는 하나의 예측 주문만 맞춘다."""
    rest = list(gold)
    matched = 0
    for o in sorted(pred, key=_key):
        hit = next((k for k, x in enumerate(rest) if _key(x) == _key(o) and abs(x["weight"] - o["weight"]) <= tol), None)
        if hit is not None:
            matched += 1
            rest.pop(hit)
    return {"matched": matched, "pred": len(pred), "gold": len(gold)}


def orders_from_fills(fills: list[dict], weights: dict[str, float], calendar: pd.DatetimeIndex) -> list[dict]:
    """우리 백테스트 체결 → 주문 목록. weights: 종목별 자리 크기.
    매수(entry)·리밸런싱 → 자리 비중, 전량 매도(exit) → 0. 신호 없는 첫날 매수는 체결일 전 거래일을 판단일로 적는다."""
    days = [d.strftime("%Y-%m-%d") for d in calendar]
    pos = {d: i for i, d in enumerate(days)}
    out: dict[tuple, dict] = {}
    for f in fills:
        date = f.get("signal_date")
        if date is None:
            i = pos.get(f["date"])
            date = days[i - 1] if i else f["date"]
        w = 0.0 if (f["side"] == "sell" and f["reason"] == "exit") else weights[f["ticker"]]
        o = {"date": date, "ticker": f["ticker"], "side": f["side"], "weight": round(w, 6)}
        out[_key(o)] = o
    return sorted(out.values(), key=_key)


def execute_orders(orders: list[dict], prices: dict[str, pd.DataFrame], calendar: pd.DatetimeIndex, *,
                   initial_cash: float, commission_rate: float, slippage_rate: float,
                   instruments: Optional[dict[str, str]] = None) -> dict:
    """주문 → {"equity": Series(calendar), "fills": [...], "ignored": [...], "unfilled": [...]}.
    calendar: 기간의 거래일 (자산 곡선의 날짜). 주문한 종목의 거래일이 빠져 있으면 합쳐서 쓴다."""
    fee, slip = commission_rate, slippage_rate
    tickers = sorted({o["ticker"] for o in orders})
    start, end = calendar[0], calendar[-1]
    cal = calendar
    for t in tickers:
        idx = prices[t].index
        cal = cal.union(idx[(idx >= start) & (idx <= end)])
    days = [d.strftime("%Y-%m-%d") for d in cal]
    opens = {t: prices[t]["Open"].reindex(cal).to_numpy(dtype=float) for t in tickers}
    closes = {t: prices[t]["Close"].astype(float).reindex(prices[t].index.union(cal)).ffill().reindex(cal)
              .to_numpy(dtype=float) for t in tickers}

    # 주문마다 체결일: 그 종목 데이터에서 판단일 다음 거래일
    by_day: dict[int, dict[str, dict]] = {}
    ignored, unfilled = [], []
    pos_of = {d: i for i, d in enumerate(cal)}
    for o in sorted(orders, key=lambda x: x["date"]):
        idx = prices[o["ticker"]].index
        k = int(idx.searchsorted(pd.Timestamp(o["date"]), side="right"))
        if k >= len(idx) or idx[k] > end:
            unfilled.append(o)
        elif idx[k] < start:
            ignored.append(o)
        else:
            by_day.setdefault(pos_of[idx[k]], {})[o["ticker"]] = o   # 같은 날 체결이면 늦게 판단한 주문이 이김

    cash = float(initial_cash)
    shares = {t: 0 for t in tickers}
    avg = {t: 0.0 for t in tickers}
    fills, eq = [], []

    def cats(t):
        c = (instruments or {}).get(t) or tax_category(t)
        if c is None:
            raise ValueError(f"{t}: 세율 분류를 알 수 없습니다.")
        return c

    for i, day in enumerate(cal):
        todo = by_day.get(i, {})
        if todo:
            px_now = {t: opens[t][i] if not math.isnan(opens[t][i]) else (closes[t][i - 1] if i else closes[t][i])
                      for t in tickers}
            eq_open = cash + sum(shares[t] * px_now[t] for t in tickers if shares[t])
            for t, o in sorted(todo.items()):                       # 넘치는 쪽 먼저 판다
                op = opens[t][i]
                excess = shares[t] * op - eq_open * o["weight"]
                if excess > 0:
                    n = shares[t] if o["weight"] <= _EPS else min(shares[t], int(math.floor(excess / op + _EPS)))
                    if n > 0:
                        px = op * (1 - slip)
                        gross = n * px
                        comm = gross * fee
                        c = cats(t)
                        tax = gross * sell_tax_rate(c, day) + max((px - avg[t]) * n, 0.0) * gain_tax_rate(c, day)
                        cash += gross - comm - tax
                        shares[t] -= n
                        if shares[t] == 0:
                            avg[t] = 0.0
                        fills.append({"date": days[i], "signal_date": o["date"], "ticker": t, "side": "sell",
                                      "shares": n, "price": round(px, 6)})
            for t, o in sorted(todo.items()):
                op = opens[t][i]
                deficit = eq_open * o["weight"] - shares[t] * op
                if deficit > 0:
                    px = op * (1 + slip)
                    n = int(math.floor(min(deficit, cash) / (px * (1 + fee)) + _EPS))
                    if n > 0:
                        gross = n * px
                        cash -= gross + gross * fee
                        avg[t] = (avg[t] * shares[t] + px * n) / (shares[t] + n)
                        shares[t] += n
                        fills.append({"date": days[i], "signal_date": o["date"], "ticker": t, "side": "buy",
                                      "shares": n, "price": round(px, 6)})
        eq.append(cash + sum(shares[t] * closes[t][i] for t in tickers if shares[t]))
    equity = pd.Series(eq, index=cal, name="equity").reindex(calendar)
    return {"equity": equity, "fills": fills, "ignored": ignored, "unfilled": unfilled}
