"""전략서 v0.2 계약(app/contracts/strategy_v0_2.py) 테스트.

평가 데이터셋은 개발용(dev)만 읽는다. 최종 평가용(test)은 개발에 쓰지 않는다(eval/README.md).
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.contracts.strategy_v0_2 import (
    AllOf,
    Compare,
    StrategyV0_2,
    iter_compares,
)


def _dataset_path() -> Path | None:
    # 호스트에서는 저장소 루트, 컨테이너에서는 /repo 에 eval/ 이 있다
    for root in (Path(__file__).resolve().parents[2], Path("/repo")):
        path = root / "eval" / "dataset.jsonl"
        if path.exists():
            return path
    return None


def _dev_ok_rows() -> list[dict]:
    path = _dataset_path()
    if path is None:
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if r["split"] == "dev" and r["type"] == "ok"]


RSI_MA5 = {
    "universe": {"tickers": ["005930"]},
    "weights": "equal",
    "rebalance": None,
    "rules": [{
        "target": "each",
        "entry": {"left": {"indicator": "rsi", "window": 14, "of": "self"},
                  "op": "cross_below", "right": {"value": 30}},
        "exit": {"left": {"indicator": "close", "of": "self"},
                 "op": "cross_below", "right": {"indicator": "ma", "window": 5, "of": "self"}},
    }],
    "constraint": {},
    "backtest": {"start": "2023-01-01", "end": "2025-12-31", "capital": 10000000, "include_dividends": False},
    "meta": {"defaults_used": ["period", "capital", "costs"],
             "evidence": {"entry": ["RSI", "30", "아래로 떨어지면", "사고"]}},
}


def _with(**changes) -> dict:
    spec = json.loads(json.dumps(RSI_MA5))
    spec.update(changes)
    return spec


@pytest.mark.skipif(_dataset_path() is None, reason="eval/dataset.jsonl 이 없다")
def test_every_dev_answer_spec_fits_the_contract():
    rows = _dev_ok_rows()
    assert len(rows) == 20
    for row in rows:
        StrategyV0_2.model_validate(row["expected_spec"])


def test_representative_example():
    spec = StrategyV0_2.model_validate(RSI_MA5)
    assert spec.is_rules_strategy
    assert spec.universe.held_tickers() == ["005930"]
    assert spec.backtest.start == date(2023, 1, 1)
    assert [c.op for c in iter_compares(spec.rules[0].entry)] == ["cross_below"]


def test_defaults_fill_missing_sections():
    spec = StrategyV0_2.model_validate({"universe": {"tickers": ["069500", "005930"]}})
    assert spec.weights == "equal"
    assert spec.rules == []
    assert not spec.is_rules_strategy
    assert spec.backtest.capital == 10_000_000
    assert spec.backtest.end == date(2025, 12, 31)
    assert spec.constraint.max_weight_per_asset is None


def test_round_trip_is_stable():
    spec = StrategyV0_2.model_validate(RSI_MA5)
    again = StrategyV0_2.model_validate(spec.model_dump(mode="json"))
    assert again == spec


def test_group_is_one_level_and_needs_two_items():
    compare = RSI_MA5["rules"][0]["entry"]
    spec = StrategyV0_2.model_validate(
        _with(rules=[{"target": "each", "entry": {"all": [compare, compare]}, "exit": None}])
    )
    group = spec.rules[0].entry
    assert isinstance(group, AllOf)
    assert all(isinstance(c, Compare) for c in iter_compares(group))

    nested = {"all": [compare, {"any": [compare, compare]}]}
    with pytest.raises(ValidationError):
        StrategyV0_2.model_validate(_with(rules=[{"target": "each", "entry": nested}]))
    with pytest.raises(ValidationError):
        StrategyV0_2.model_validate(_with(rules=[{"target": "each", "entry": {"any": [compare]}}]))


@pytest.mark.parametrize(
    ("label", "changes"),
    [
        ("빈 rule", {"rules": [{"target": "each", "entry": None, "exit": None}]}),
        ("tickers 와 selector 를 같이",
         {"universe": {"tickers": ["005930"],
                       "selector": {"kind": "sector", "value": "보험", "count": 5, "method": "market_cap"}}}),
        ("universe 가 비었음", {"universe": {}}),
        ("종목코드 형식", {"universe": {"tickers": ["5930"]}}),
        ("종목코드 소문자", {"universe": {"tickers": ["0000d0"]}}),
        ("영문 6자는 종목코드가 아님", {"universe": {"tickers": ["NASDAQ"]}}),
        ("종목코드 중복", {"universe": {"tickers": ["005930", "005930"]}}),
        ("비중 0", {"weights": {"005930": 0}}),
        ("비중 1 초과", {"weights": {"005930": 1.2}}),
        ("universe 밖 비중", {"weights": {"000660": 0.5}}),
        ("universe 밖 target", {"rules": [dict(RSI_MA5["rules"][0], target="000660")]}),
        ("모르는 리밸런싱", {"rebalance": "RB_YEARLY"}),
        ("모르는 칸", {"signal_rules": {}}),
        ("기간이 거꾸로", {"backtest": {"start": "2025-12-31", "end": "2023-01-01"}}),
        ("최대 비중 범위", {"constraint": {"max_weight_per_asset": 1.5}}),
        ("모르는 기본값 이름", {"meta": {"defaults_used": ["fees"]}}),
    ],
)
def test_rejects_wrong_shape(label, changes):
    with pytest.raises(ValidationError):
        StrategyV0_2.model_validate(_with(**changes))


@pytest.mark.parametrize(
    "operand",
    [
        {"indicator": "macd", "of": "self"},
        {"indicator": "ma", "window": 0, "of": "self"},
        {"indicator": "close", "of": "NASDAQ"},
        {"indicator": "close", "of": "self", "extra": 1},
    ],
)
def test_rejects_unknown_operand(operand):
    rule = dict(RSI_MA5["rules"][0])
    rule["exit"] = {"left": operand, "op": "<", "right": {"value": 1}}
    with pytest.raises(ValidationError):
        StrategyV0_2.model_validate(_with(rules=[rule]))


def test_rejects_unknown_operator():
    rule = dict(RSI_MA5["rules"][0])
    rule["entry"] = dict(rule["entry"], op="==")
    with pytest.raises(ValidationError):
        StrategyV0_2.model_validate(_with(rules=[rule]))


def test_meaning_checks_are_left_to_stage_3():
    # 허용 밖 기간(7일선), 단위가 다른 비교, 손절을 살 때 쓴 것은 모양으로는 맞다. 검사 3단계가 막는다
    rule = {
        "target": "each",
        "entry": {"left": {"indicator": "entry_return", "of": "self"}, "op": ">=", "right": {"value": 0.1}},
        "exit": {"left": {"indicator": "rsi", "window": 14, "of": "self"},
                 "op": "<", "right": {"indicator": "ma", "window": 7, "of": "self"}},
    }
    StrategyV0_2.model_validate(_with(rules=[rule], weights={"005930": 1.0}))


def test_selector_without_resolution_has_no_held_tickers():
    spec = StrategyV0_2.model_validate({
        "universe": {"selector": {"kind": "sector", "value": "보험", "market": "KOSPI",
                                  "count": 5, "method": "market_cap"}},
    })
    assert spec.universe.held_tickers() is None
    resolved = StrategyV0_2.model_validate({
        "universe": {"selector": {"kind": "market", "market": "KOSPI", "count": "all"},
                     "resolved_tickers": ["005930", "000660"]},
    })
    assert resolved.universe.held_tickers() == ["005930", "000660"]
