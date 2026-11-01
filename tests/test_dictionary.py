"""스키마 export · 사전 일관성 · 종목 유형 → 세율 연결."""
import json
import re
from typing import get_args

import pytest

from conftest import base_spec_dict, make_ohlcv
from core.backtest import run_backtest
from core.dictionary import INDICATORS, OPERATORS, asset_types, load_expressions, load_tickers
from core.indicators import INDICATOR_IDS
from core.schema import SCHEMA_PATH, Condition, DemoSpec, IndicatorId, Operator, build_json_schema, dump_json_schema


def test_exported_schema_is_up_to_date():
    """schema/demospec.json 은 생성물. 다르면 `python -m core.schema` 로 다시 만든다."""
    assert SCHEMA_PATH.read_text(encoding="utf-8") == dump_json_schema(build_json_schema())


def test_schema_enums_are_the_dictionary():
    s = build_json_schema()
    cond = s["$defs"]["Condition"]["properties"]
    assert cond["left"]["enum"] == INDICATOR_IDS
    assert cond["op"]["enum"] == list(OPERATORS)
    uni = s["$defs"]["UniverseItem"]["properties"]
    assert uni["ticker"]["enum"] == list(load_tickers())
    assert uni["name"]["enum"] == [t.name for t in load_tickers().values()]
    assert list(get_args(IndicatorId)) == INDICATOR_IDS == list(INDICATORS)
    assert list(get_args(Operator)) == list(OPERATORS)


def test_tickers_seed():
    t = load_tickers()
    assert len(t) == 20
    types = [x.asset_type for x in t.values()]
    assert types.count("stock") == 10 and types.count("etf") == 10
    assert all(re.fullmatch(r"\d{6}", k) for k in t)


def _expand(obj, n):
    return json.loads(json.dumps(obj).replace("{n}", str(n)))


def test_expression_mappings_use_only_dictionary_values():
    ex = load_expressions()
    for r in ex["direction_rules"]["rules"]:
        assert r["op"] in OPERATORS
        Condition.model_validate(r["example"]["condition"])
    for a in ex["indicator_aliases"]:
        assert a["id"] in INDICATORS
    for p in ex["phrases"]:
        if "condition" in p:
            ns = (5, 10, 20, 60, 120) if "{n}" in json.dumps(p["condition"]) else (None,)
            for n in ns:
                Condition.model_validate(_expand(p["condition"], n))
        if p["verdict"] == "reject":
            assert p["reject_code"] in ("FUTURE_REF", "UNSUPPORTED_CONCEPT", "UNSUPPORTED_ACTION", "NO_EXIT_RULE")


def test_direction_rule_event_vs_state():
    rules = {r["op"]: r["patterns"] for r in load_expressions()["direction_rules"]["rules"]}
    assert "아래로 떨어지면" in rules["cross_below"] and "아래로 내려가면" in rules["cross_below"]
    assert "아래면" in rules["lt"] and "아래일 때" in rules["lt"]
    assert "올라가면" in rules["cross_above"] and "돌파하면" in rules["cross_above"]
    assert "위면" in rules["gt"] and "위일 때" in rules["gt"]
    assert "이하면" in rules["lt"] and "이상이면" in rules["gt"]     # 이하=lt, 이상=gt (clarify 안 함)


@pytest.mark.parametrize("ticker,expected_tax", [("005930", 0.0020), ("069500", 0.0)])
def test_backtest_tax_follows_tickers_json(ticker, expected_tax):
    """asset_types 를 넘기지 않으면 tickers.json 의 asset_type 으로 세율을 고른다."""
    assert asset_types()[ticker] == ("stock" if expected_tax else "etf")
    d = base_spec_dict(
        universe=[{"ticker": ticker, "name": "x", "weight": 1.0}],
        entry={"logic": "all", "conditions": [{"left": "CLOSE", "op": "cross_below", "right": 100}]},
        exit={"logic": "any", "conditions": [{"left": "CLOSE", "op": "cross_above", "right": 120}],
              "stop_loss": None, "take_profit": None, "max_hold_days": None},
        backtest={"start": "2000-01-01", "end": "2100-12-31", "initial_cash": 1_000_000},
    )
    df = make_ohlcv([110, 105, 99, 101, 108, 115, 121, 118], opens=[110, 109, 104, 97, 102, 107, 116, 125])
    res = run_backtest(DemoSpec.model_validate(d), data={ticker: df})
    tr = res.trades[0]
    assert tr["exit_tax"] == pytest.approx(tr["shares"] * 125.0 * expected_tax)
    assert res.notes == []


def test_backtest_unknown_ticker_falls_back_with_note():
    d = base_spec_dict()  # TEST01 은 사전에 없음
    res = run_backtest(DemoSpec.model_validate(d), data={"TEST01": make_ohlcv([100] * 30)})
    assert res.notes == ["TEST01: 종목 유형 미지정 → 'stock' 세율 적용"]
