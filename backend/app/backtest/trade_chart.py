# 백테스트 결과 화면의 매매 지점 차트 자료(docs/v0.2-plan.md 11단계).
# DB 를 모르는 순수 함수이고 stdlib 만 쓴다.
#
# 설명을 docstring 이 아니라 주석에 두는 이유는 features.py 와 같다(as_of 가드 사정권).
# 현서 님 demo 의 trade_chart 를 옮겼다. 종목마다
#   - close        기간 달력의 날마다 그날(또는 직전) 종가
#   - price_lines  규칙이 그 종목에서 보는 가격 단위 지표(이동평균). 종가와 같은 칸에 그린다
#   - panels       가격 단위가 아닌 지표(RSI, N일 수익률, 변동성, 거래량 배수)마다 보조 칸.
#                  refs 는 규칙이 그 지표와 비교한 숫자(기준선)
#   - buys, sells  체결일 · 체결가 · 판단일(signal_date)
# 지표 값은 엔진과 같은 signals.indicator_series 로 계산한다. 다른 종목을 보는 조건과
# 보유 상태 지표(산 가격 대비 수익률, 보유 일수)는 그리지 않는다.
from __future__ import annotations

import bisect
from collections.abc import Mapping, Sequence
from datetime import date

from app.backtest import signals
from app.contracts.strategy_v0_2 import AllOf, AnyOf, Compare, Indicator, StrategyV0_2, Value

PRICE_UNIT = ("ma",)
PANEL = ("rsi", "ret", "vol", "volume_ratio")


def label(indicator: str, window: int | None) -> str:
    return {
        "ma": f"{window}일 이평",
        "rsi": f"RSI({window})",
        "ret": f"{window}일 수익률",
        "vol": f"{window}일 변동성",
        "volume_ratio": f"거래량 배수({window}일 평균 대비)",
    }.get(indicator, indicator)


def build(
    spec: StrategyV0_2,
    bars: Mapping[str, Sequence[Mapping]],
    calendar: Sequence[date],
    fills: Sequence[Mapping],
    names: Mapping[str, str],
) -> list[dict]:
    tickers = sorted(spec.universe.held_tickers() or [])
    used = _indicators_and_refs(spec)
    out = []
    for t in tickers:
        rows = bars.get(t) or []
        dates = [r["trade_date"] for r in rows]
        closes = [float(r["close"]) for r in rows]
        volumes = [float(r.get("volume") or 0.0) for r in rows]
        picks = [bisect.bisect_right(dates, d) - 1 for d in calendar]

        def on_calendar(values: Sequence[float | None], picks=picks) -> list[float | None]:
            return [None if k < 0 or values[k] is None else round(values[k], 4) for k in picks]

        price_lines, panels = [], []
        for (indicator, window), refs in used.items():
            values = on_calendar(signals.indicator_series(closes, volumes, indicator, window))
            line = {"label": label(indicator, window), "values": values}
            if indicator in PRICE_UNIT:
                price_lines.append(line)
            else:
                panels.append({"title": label(indicator, window), "lines": [line], "refs": sorted(refs)})
        mine = [f for f in fills if f["ticker"] == t]
        out.append(
            {
                "ticker": t,
                "name": names.get(t, t),
                "close": on_calendar(closes),
                "price_lines": price_lines,
                "panels": panels,
                "buys": [_point(f) for f in mine if f["side"] == "buy"],
                "sells": [_point(f) for f in mine if f["side"] == "sell"],
            }
        )
    return out


def _point(fill: Mapping) -> dict:
    signal = fill.get("decided_on")
    return {
        "date": fill["date"].isoformat(),
        "price": round(float(fill["price"]), 2),
        "signal_date": signal.isoformat() if signal and signal != fill["date"] else None,
    }


def _indicators_and_refs(spec: StrategyV0_2) -> dict[tuple[str, int | None], set[float]]:
    # 규칙이 그 종목 자신(of = self)에서 보는 지표와, 그 지표와 비교한 숫자
    out: dict[tuple[str, int | None], set[float]] = {}
    for rule in spec.rules:
        for cond in (rule.entry, rule.exit):
            for c in _compares(cond):
                sides = [s for s in (c.left, c.right) if isinstance(s, Indicator)]
                for s in sides:
                    if s.of != "self" or s.indicator not in (*PRICE_UNIT, *PANEL):
                        continue
                    refs = out.setdefault((s.indicator, s.window), set())
                    other = c.right if s is c.left else c.left
                    if isinstance(other, Value):
                        refs.add(float(other.value))
    return out


def _compares(cond) -> list[Compare]:
    if cond is None:
        return []
    if isinstance(cond, Compare):
        return [cond]
    if isinstance(cond, AllOf):
        return list(cond.all)
    if isinstance(cond, AnyOf):
        return list(cond.any)
    return []
