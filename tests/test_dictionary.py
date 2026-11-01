"""v0.2 전략서 형식 · 사전 · 종목 목록 일관성."""
import json
from typing import get_args

import pytest
from pydantic import ValidationError

from conftest import strategy_dict
from core.dictionary import INDEX_SYMBOLS, INDICATORS, OPERATORS, REBALANCE, load_expressions, load_tickers, tax_category
from core.indicators import COMPUTABLE
from core.schema import SCHEMA_PATH, IndicatorName, Op, Strategy, build_json_schema, dump_json_schema


def test_exported_schema_is_up_to_date():
    """schema/strategy-v0.2.json 은 생성물. 다르면 `python -m core.schema` 로 다시 만든다."""
    assert SCHEMA_PATH.read_text(encoding="utf-8") == dump_json_schema(build_json_schema())


def test_dictionary_is_v02():
    assert list(INDICATORS) == ["close", "ma", "rsi", "ret", "vol", "volume_ratio", "vix",
                                "entry_return", "holding_days"]
    assert list(get_args(IndicatorName)) == list(INDICATORS)
    assert INDICATORS["ma"]["windows"] == [5, 10, 20, 60, 120]
    assert INDICATORS["rsi"]["windows"] == [14]
    assert INDICATORS["ret"]["windows"] == [1, 5, 20, 60]
    assert INDICATORS["vol"]["windows"] == [20]
    assert INDICATORS["volume_ratio"]["windows"] == [20]
    for name in ("close", "vix", "entry_return", "holding_days"):
        assert INDICATORS[name]["windows"] == [None]
    assert list(OPERATORS) == ["<=", "<", ">=", ">", "cross_above", "cross_below"]
    assert list(get_args(Op)) == list(OPERATORS)
    # 시장 데이터 지표는 모두 계산 함수가 있고, 포지션 지표는 엔진이 계산한다
    assert {n for n, m in INDICATORS.items() if not m.get("position") and n != "vix"} == set(COMPUTABLE)


@pytest.mark.parametrize("removed", ["EMA_12", "ema", "bb_upper", "BB_UPPER_20", "HIGH_N_20", "high_n", "open",
                                     "OPEN", "high", "low", "volume", "sma"])
def test_removed_indicators_are_not_accepted(removed):
    d = strategy_dict()
    d["rules"]["entry"]["left"] = {"indicator": removed, "window": 20, "of": "self"}
    with pytest.raises(ValidationError):
        Strategy.model_validate(d)


def test_of_targets():
    assert INDEX_SYMBOLS == {"KOSPI200": "KS200", "KOSDAQ": "KQ11", "VIX": "VIX"}
    assert INDICATORS["volume_ratio"]["of"] == ["self", "ticker"]          # 지수에는 거래량 없음
    assert "KOSDAQ" in INDICATORS["close"]["of"] and "KOSDAQ" in INDICATORS["vol"]["of"]
    assert INDICATORS["vix"]["of"] == ["VIX"]
    assert INDICATORS["entry_return"]["of"] == ["self"] and INDICATORS["entry_return"]["exit_only"]
    assert INDICATORS["holding_days"]["of"] == ["self"] and INDICATORS["holding_days"]["exit_only"]


def test_rebalance_options():
    assert list(REBALANCE) == ["monthly_first", "monthly_mid", "quarterly", "weekly", "drift_5pp"]


def test_tickers_have_market_kind_tax_type():
    tickers = load_tickers()
    assert tickers, "종목 목록이 비어 있다"
    for t in tickers.values():
        assert t.market in ("KOSPI", "KOSDAQ")
        assert t.kind in ("STOCK", "ETF")
        if t.kind == "ETF":
            assert t.tax_type in ("DOMESTIC_EQUITY", "OTHER")
        else:
            assert t.tax_type is None
    assert tax_category("005930") == "KOSPI_STOCK"
    assert tax_category("069500") == "ETF_DOMESTIC_EQUITY"   # KODEX 200
    assert tax_category("153130") == "ETF_OTHER"             # KODEX 단기채권 (채권형)
    assert tax_category("122630") == "ETF_OTHER"             # KODEX 레버리지 (v0.2: 레버리지는 그 밖의 ETF)
    assert tax_category("NOPE") is None


def test_expressions_map_only_to_dictionary_values():
    ex = load_expressions()
    for rule in ex["direction_rules"]["rules"]:
        assert rule["op"] in OPERATORS
    for a in ex["indicator_aliases"]:
        assert a["indicator"] in INDICATORS
        assert a.get("window") in INDICATORS[a["indicator"]]["windows"]
    for p in ex["phrases"]:
        if "condition" in p:
            # {n} 자리는 대표값으로 채워 형식 검사
            raw = json.dumps(p["condition"]).replace('"{n}"', "20").replace("{n}", "20").replace('"{p}"', "10")
            raw = raw.replace('"-{p}"', "-10")
            d = strategy_dict()
            d["rules"]["entry"] = json.loads(raw)
            Strategy.model_validate(d)


def test_v02_sample_round_trip():
    """v0.2 문서의 예시 그대로."""
    rules = {
        "target": "each",
        "entry": {"left": {"indicator": "rsi", "window": 14, "of": "self"}, "op": "cross_below",
                  "right": {"value": 30}},
        "exit": {"left": {"indicator": "close", "of": "self"}, "op": "cross_below",
                 "right": {"indicator": "ma", "window": 5, "of": "self"}},
    }
    s = Strategy.model_validate(strategy_dict(rules=rules))
    out = s.model_dump(mode="json", exclude_none=True)["rules"]
    assert out == rules
