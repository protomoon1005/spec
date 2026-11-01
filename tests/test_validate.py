"""v0.2 검사 1~3단계 (모양 · 존재 · 말이 되는지). 4·5단계는 다음 단계에서 넣는다."""
import copy

import pytest

from conftest import cmp, ind, strategy_dict
from core.validate import validate


def _samsung(**kw):
    kw.setdefault("assets", [{"ticker": "005930", "name": "삼성전자", "weight": None}])
    d = strategy_dict(**kw)
    d["backtest"] = {"start": "2023-01-01", "end": "2025-12-31", "initial_cash": 10_000_000,
                     "commission_rate": 0.00015, "slippage_rate": 0.0005}
    return d


def _codes(rep):
    return [(i.stage, i.code, i.kind) for i in rep.issues]


def test_v02_sample_passes_stages_1_to_3():
    rep = validate(_samsung())
    assert rep.verdict == "ok", [str(i) for i in rep.issues]
    assert [(s.stage, s.status) for s in rep.stages] == [(1, "passed"), (2, "passed"), (3, "passed"),
                                                          (4, "not_implemented"), (5, "not_implemented")]


# ---------------------------------------------------------------- 1. 모양

@pytest.mark.parametrize("mutate", [
    lambda d: d["assets"][0].update(weight=1.5),
    lambda d: d["rules"]["entry"].update(op="lt"),
    lambda d: d["rules"]["entry"]["left"].update(indicator="ema"),
    lambda d: d.update(max_weight=0),
    lambda d: d["backtest"].update(start="2025-02-30"),
])
def test_shape_errors_block_at_stage_1(mutate):
    d = _samsung()
    mutate(d)
    rep = validate(d)
    assert rep.verdict == "reject" and rep.failed_stage == 1
    assert rep.stages[1].status == "skipped"


# ---------------------------------------------------------------- 2. 존재

@pytest.mark.parametrize("mutate,code", [
    (lambda d: d["assets"][0].update(ticker="999999"), "UNKNOWN_TICKER"),
    (lambda d: d["assets"][0].update(name="SK하이닉스"), "NAME_MISMATCH"),
    (lambda d: d["rules"]["exit"]["right"].update(window=7), "BAD_WINDOW"),
    (lambda d: d["rules"]["entry"]["left"].update(window=9), "BAD_WINDOW"),
    (lambda d: d["rules"]["entry"].update(left=ind("volume_ratio", 20, of="KOSDAQ")), "BAD_OF"),
    (lambda d: d["rules"]["entry"].update(left=ind("vix", of="self")), "BAD_OF"),
    (lambda d: d["rules"]["entry"].update(left=ind("close", of="000000")), "UNKNOWN_TICKER"),
])
def test_existence_blocks_at_stage_2(mutate, code):
    d = _samsung()
    mutate(d)
    rep = validate(d)
    assert rep.verdict == "reject" and rep.failed_stage == 2
    assert (2, code, "block") in _codes(rep)


def test_other_ticker_and_index_are_allowed():
    d = _samsung()
    d["rules"]["entry"] = {"all": [cmp(ind("close", of="KOSPI200"), ">", ind("ma", 20, of="KOSPI200")),
                                   cmp(ind("rsi", 14, of="000660"), "<", 30),
                                   cmp(ind("vix", of="VIX"), "<", 25)]}
    assert validate(d).verdict == "ok"


# ---------------------------------------------------------------- 3. 말이 되는지

@pytest.mark.parametrize("entry,code", [
    (cmp(ind("rsi", 14), "<", ind("ma", 5)), "UNIT_MISMATCH"),
    (cmp(ind("rsi", 14), "cross_above", 150), "OUT_OF_RANGE"),
    (cmp(ind("close"), "<", ind("close")), "SELF_COMPARE"),
    (cmp(ind("entry_return"), ">=", 10), "EXIT_ONLY_IN_ENTRY"),
    (cmp(ind("holding_days"), ">=", 5), "EXIT_ONLY_IN_ENTRY"),
])
def test_logic_blocks_at_stage_3(entry, code):
    rep = validate(_samsung(rules={"target": "each", "entry": entry, "exit": cmp(ind("close"), "cross_below", ind("ma", 5))}))
    assert rep.verdict == "reject" and rep.failed_stage == 3
    assert (3, code, "block") in _codes(rep)


def test_ret_and_entry_return_share_unit():
    exit_ = cmp(ind("entry_return"), "<", ind("ret", 20, of="KOSPI200"))
    assert validate(_samsung(rules={"target": "each", "entry": cmp(ind("rsi", 14), "<", 30), "exit": exit_})).verdict == "ok"


def test_entry_always_implies_exit_asks():
    """'RSI 30 밑이면 사고, 40 밑이면 팔아' → 되묻기."""
    rules = {"target": "each", "entry": cmp(ind("rsi", 14), "<", 30), "exit": cmp(ind("rsi", 14), "<", 40)}
    rep = validate(_samsung(rules=rules))
    assert rep.verdict == "clarify"
    assert (3, "ALWAYS_BOTH", "clarify") in _codes(rep)


@pytest.mark.parametrize("assets,max_weight,code", [
    ([("005930", "삼성전자", 0.7), ("000660", "SK하이닉스", 0.5)], None, "WEIGHT_SUM"),
    ([("005930", "삼성전자", 0.5), ("000660", "SK하이닉스", 0.3)], 0.4, "WEIGHT_OVER_MAX"),
    ([("005930", "삼성전자", 0.5), ("000660", "SK하이닉스", None)], None, "PARTIAL_WEIGHTS"),
])
def test_weight_problems_ask(assets, max_weight, code):
    d = _samsung(assets=[{"ticker": t, "name": n, "weight": w} for t, n, w in assets], max_weight=max_weight)
    rep = validate(d)
    assert rep.verdict == "clarify" and (3, code, "clarify") in _codes(rep)


@pytest.mark.parametrize("side,code", [("exit", "NO_EXIT"), ("entry", "NO_ENTRY")])
def test_one_sided_rules_ask(side, code):
    d = _samsung()
    d["rules"][side] = None
    rep = validate(d)
    assert rep.verdict == "clarify" and (3, code, "clarify") in _codes(rep)


def test_allocation_only_needs_rebalance_and_dates_in_order():
    rep = validate(_samsung(rules=None, rebalance=None))
    assert (3, "NO_REBALANCE", "block") in _codes(rep)
    assert validate(_samsung(rules=None, rebalance="monthly_first")).verdict == "ok"
    d = _samsung()
    d["backtest"]["start"] = "2026-01-01"
    assert (3, "DATE_ORDER", "block") in _codes(validate(d))


def test_nested_groups_are_checked_too():
    d = _samsung()
    d["rules"]["entry"] = {"all": [cmp(ind("rsi", 14), "<", 30),
                                   {"any": [cmp(ind("close"), ">", ind("ma", 20)), cmp(ind("rsi", 14), ">", 101)]}]}
    rep = validate(d)
    assert (3, "OUT_OF_RANGE", "block") in _codes(rep)
    assert any("entry.all[1].any[1]" in i.field for i in rep.issues)
