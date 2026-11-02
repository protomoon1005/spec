# 비교 대상(LLM 이 코드를 직접 짜는 방식) 모듈. Ollama 없이 응답 해석 · 체결 · 비교 · 샌드박스를 본다.
from __future__ import annotations

from datetime import date

import pandas as pd

from app.backtest import taxes
from app.baseline import codegen, orders, sandbox

CODE = """import pandas as pd

def strategy(prices, start, end):
    df = prices["005930"]
    first = df.index[df.index >= start][0]
    before = df.index[df.index < first][-1]
    return [{"date": before.strftime("%Y-%m-%d"), "ticker": "005930", "side": "buy", "weight": 0.5}]
"""


def _frame(n=6, start="2024-01-01"):
    idx = pd.bdate_range(start, periods=n)
    opens = [100.0 + i for i in range(n)]
    return pd.DataFrame(
        {"Open": opens, "High": opens, "Low": opens, "Close": opens, "Volume": [1.0] * n}, index=idx
    )


def test_parse_code_block_wins_and_markers():
    raw = f"설명\n```python\n{CODE}```\n"
    kind, code, _ = codegen.parse(raw)
    assert kind == "code" and "def strategy" in code
    assert codegen.parse("NEED_CLARIFICATION: 어떤 종목인가요?")[0:3:2] == ("clarify", "어떤 종목인가요?")
    assert codegen.parse("`CANNOT_IMPLEMENT: 지수 없음`")[0] == "reject"
    assert codegen.parse("모르겠습니다")[0] == "error"


def test_prompt_lists_only_given_tickers():
    prompt = codegen.build_prompt([("005930", "삼성전자", "주식")], "2023-01-01", "2025-12-31")
    assert '"005930": 삼성전자' in prompt and "VIX" in prompt


def test_execute_orders_next_open_one_share_units_and_tax():
    frame = _frame()
    days = frame.index
    o = [
        {"date": days[0].strftime("%Y-%m-%d"), "ticker": "005930", "side": "buy", "weight": 0.5},
        {"date": days[2].strftime("%Y-%m-%d"), "ticker": "005930", "side": "sell", "weight": 0.0},
    ]
    fills = orders.execute_orders(
        o, {"005930": frame}, date(2024, 1, 1), date(2024, 12, 31), 10_000, {"005930": taxes.STOCK}
    )
    assert [(f["date"], f["side"], f["shares"]) for f in fills] == [
        (days[1].date(), "buy", 49),
        (days[3].date(), "sell", 49),
    ]
    assert fills[0]["target_weight"] == 0.5 and fills[1]["target_weight"] == 0.0


def test_execute_orders_skips_fills_outside_period_and_keeps_last_order():
    frame = _frame()
    days = frame.index
    o = [
        {"date": days[1].strftime("%Y-%m-%d"), "ticker": "005930", "side": "buy", "weight": 0.3},
        {"date": days[1].strftime("%Y-%m-%d"), "ticker": "005930", "side": "buy", "weight": 0.6},
        {"date": days[-1].strftime("%Y-%m-%d"), "ticker": "005930", "side": "sell", "weight": 0.0},
    ]
    fills = orders.execute_orders(
        o, {"005930": frame}, date(2024, 1, 1), date(2024, 12, 31), 10_000, {"005930": taxes.STOCK}
    )
    assert len(fills) == 1 and fills[0]["target_weight"] == 0.6


def test_same_fills_tolerance():
    a = [{"date": date(2024, 1, 2), "ticker": "005930", "side": "buy", "target_weight": 0.5}]
    b = [{"date": date(2024, 1, 2), "ticker": "005930", "side": "buy", "target_weight": 0.504}]
    c = [{"date": date(2024, 1, 2), "ticker": "005930", "side": "buy", "target_weight": 0.51}]
    assert orders.same_fills(a, b)
    assert not orders.same_fills(a, c)
    assert not orders.same_fills(a, [])


def test_sandbox_runs_code_and_blocks_imports():
    frames = {"005930": _frame(start="2023-12-25", n=10)}
    run = sandbox.run_code(CODE, frames, "2024-01-01", "2024-12-31", known={"005930"})
    assert run.ok, run.error
    assert run.orders[0]["ticker"] == "005930"
    bad = sandbox.run_code(
        "import os\ndef strategy(p, s, e):\n    return []\n", frames, "2024-01-01", "2024-12-31"
    )
    assert not bad.ok and bad.stage == "check"


def test_equity_curve_replays_fills_with_cash_and_closes():
    frame = _frame()
    days = frame.index
    o = [{"date": days[0].strftime("%Y-%m-%d"), "ticker": "005930", "side": "buy", "weight": 0.5}]
    fills = orders.execute_orders(
        o, {"005930": frame}, date(2024, 1, 1), date(2024, 12, 31), 10_000, {"005930": taxes.STOCK}
    )
    curve = orders.equity_curve(fills, {"005930": frame}, [d.date() for d in days], 10_000)
    assert curve[0] == (days[0].date(), 10_000)
    shares, cash = fills[0]["shares"], fills[0]["cash_after"]
    assert curve[-1][1] == cash + shares * frame["Close"].iloc[-1]
