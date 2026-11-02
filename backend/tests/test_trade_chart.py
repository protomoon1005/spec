"""매매 지점 차트 자료(app/backtest/trade_chart.py)와 결과 지문(rules_service.result_hash). DB 없이 본다."""

from __future__ import annotations

from datetime import date, timedelta

from app.backtest import rules_engine, rules_service, taxes, trade_chart
from app.contracts.strategy_v0_2 import StrategyV0_2

START = date(2024, 1, 1)


def _days(n):
    return [START + timedelta(days=i) for i in range(n)]


def _bars(closes):
    return [
        {"trade_date": d, "open": c, "close": c, "volume": 1000.0}
        for d, c in zip(_days(len(closes)), closes, strict=True)
    ]


SPEC = StrategyV0_2.model_validate(
    {
        "universe": {"tickers": ["005930"]},
        "rules": [
            {
                "target": "each",
                "entry": {
                    "left": {"indicator": "rsi", "window": 3, "of": "self"},
                    "op": "<",
                    "right": {"value": 30},
                },
                "exit": {
                    "left": {"indicator": "close", "of": "self"},
                    "op": "cross_below",
                    "right": {"indicator": "ma", "window": 2, "of": "self"},
                },
            }
        ],
        "backtest": {"start": "2024-01-01", "end": "2024-12-31", "capital": 1_000_000},
    }
)
CLOSES = [100, 90, 80, 70, 75, 85, 95, 90, 80, 85, 90, 88]


def _run():
    bars = {"005930": _bars(CLOSES)}
    result = rules_engine.run_rules(SPEC, bars, _days(len(CLOSES)), {"005930": taxes.STOCK})
    return bars, result


def test_chart_has_close_price_lines_panels_and_fill_points():
    bars, result = _run()
    chart = trade_chart.build(SPEC, bars, _days(len(CLOSES)), result.fills, {"005930": "삼성전자"})
    assert len(chart) == 1
    c = chart[0]
    assert c["name"] == "삼성전자" and c["close"] == [float(x) for x in CLOSES]
    assert [ln["label"] for ln in c["price_lines"]] == ["2일 이평"]
    assert [(p["title"], p["refs"]) for p in c["panels"]] == [("RSI(3)", [30.0])]
    assert len(c["buys"]) == sum(f["side"] == "buy" for f in result.fills) > 0
    # 체결은 판단 다음 날이라 신호일이 따로 남는다
    assert all(p["signal_date"] and p["signal_date"] < p["date"] for p in c["buys"])


def test_allocation_strategy_chart_has_only_close_and_points():
    spec = StrategyV0_2.model_validate(
        {"universe": {"tickers": ["005930"]}, "backtest": {"start": "2024-01-01", "end": "2024-12-31"}}
    )
    chart = trade_chart.build(spec, {"005930": _bars(CLOSES)}, _days(len(CLOSES)), [], {})
    assert chart[0]["price_lines"] == [] and chart[0]["panels"] == [] and chart[0]["buys"] == []


def test_result_hash_is_stable_and_ignores_repro():
    window = {"series": [{"date": "2024-01-01", "strategy": 1.0}], "fills": [{"date": date(2024, 1, 2)}]}
    h = rules_service.result_hash(window)
    assert h == rules_service.result_hash({**window, "repro": {"match": True}})
    assert h != rules_service.result_hash({**window, "fills": []})
