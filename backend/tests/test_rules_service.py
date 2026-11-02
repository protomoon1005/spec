"""조건형 백테스트를 DB 의 가격 스냅샷으로 돌린다(app/backtest/rules_service.py).

테스트 DB 에 가격이 있어야 한다(scripts/build_db.sh 가 넣는다). 그래서 requires_backfill 이다.
"""
from __future__ import annotations

import pytest

from app.backtest.rules_service import MARKET_PROXY_NOTE, run_rules_backtest
from app.contracts.strategy_v0_2 import StrategyV0_2

pytestmark = pytest.mark.requires_backfill

REPRESENTATIVE = {
    "universe": {"tickers": ["005930"]},
    "rules": [{
        "target": "each",
        "entry": {"left": {"indicator": "rsi", "window": 14, "of": "self"},
                  "op": "cross_below", "right": {"value": 30}},
        "exit": {"left": {"indicator": "close", "of": "self"},
                 "op": "cross_below", "right": {"indicator": "ma", "window": 5, "of": "self"}},
    }],
}


def test_representative_example_runs_and_repeats():
    # 제안서 대표 예시 "RSI 30 아래로 떨어지면 사고, 5일선 깨지면 팔아줘" (삼성전자, 기본 기간)
    spec = StrategyV0_2.model_validate(REPRESENTATIVE)
    first = run_rules_backtest(spec)
    assert first.metrics["round_trips"] == len(first.trades) > 0
    assert all(f["date"] > f["decided_on"] for f in first.fills)
    assert first.equity[0][0] >= spec.backtest.start and first.equity[-1][0] <= spec.backtest.end
    assert set(first.benchmarks) == {"hold_same", "hold_market_proxy"}
    assert MARKET_PROXY_NOTE in first.notes
    assert run_rules_backtest(spec).to_dict() == first.to_dict()


def test_stock_and_etf_in_one_strategy():
    # 개별주 하나와 ETF 하나로 돈다(pivot-plan 3단계 끝난 기준). ETF 는 그 밖의 ETF(채권)
    spec = StrategyV0_2.model_validate({**REPRESENTATIVE, "universe": {"tickers": ["005930", "114260"]}})
    result = run_rules_backtest(spec)
    assert result.names["114260"] == "KODEX 국고채3년"
    assert result.equity
