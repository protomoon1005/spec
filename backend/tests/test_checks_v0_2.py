"""전략서 v0.2 검사 1~3단계(app/m2/checks_v0_2.py) 테스트. 가격 범위는 가짜로 넣는다."""

from __future__ import annotations

import copy
from datetime import date

from app.m2 import checks_v0_2 as chk

BASE = {
    "universe": {"tickers": ["005930"]},
    "weights": "equal",
    "rules": [
        {
            "target": "each",
            "entry": {
                "left": {"indicator": "rsi", "window": 14, "of": "self"},
                "op": "cross_below",
                "right": {"value": 30},
            },
            "exit": {
                "left": {"indicator": "close", "of": "self"},
                "op": "cross_below",
                "right": {"indicator": "ma", "window": 5, "of": "self"},
            },
        }
    ],
    "constraint": {},
    "backtest": {"start": "2023-01-01", "end": "2025-12-31", "capital": 10000000, "include_dividends": False},
}


def _spec(**rule_changes):
    spec = copy.deepcopy(BASE)
    spec["rules"][0].update(rule_changes)
    return spec


def _cmp(left, op, right):
    return {"left": left, "op": op, "right": right}


RSI = {"indicator": "rsi", "window": 14, "of": "self"}


def _existence(spec, coverage, first=date(2023, 1, 2), latest=date(2026, 10, 30)):
    return chk.check_existence(
        spec,
        known_tickers=lambda ts: {t for t in ts if t != "999999"},
        coverage=lambda ts, s, e: {t: coverage[t] for t in ts if t in coverage},
        first_trade_day=lambda s, e: first,
        latest_trade_day=lambda: latest,
    )


OLD = {"first": date(2021, 11, 2), "last": date(2025, 12, 30), "before_start": 289, "in_period": 731}


def test_clean_spec_passes_all_stages():
    findings = chk.check_shape(BASE) + _existence(BASE, {"005930": OLD}) + chk.check_logic(BASE)
    assert findings == []
    assert [s["result"] for s in chk.stage_results(findings)] == ["pass", "pass", "pass"]


def test_shape_error_blocks_stage_1_and_later_stages_do_not_run():
    findings = chk.check_shape({**BASE, "weights": {"005930": 2}})
    assert [(f.stage, f.kind) for f in findings] == [(1, "block")]
    assert [s["result"] for s in chk.stage_results(findings)] == ["block", "not_run", "not_run"]


def test_existence():
    listed_later = {
        "first": date(2024, 5, 8),
        "last": date(2025, 12, 30),
        "before_start": 0,
        "in_period": 401,
    }
    spec = {**BASE, "universe": {"tickers": ["005930", "443060", "999999"]}}
    found = _existence(spec, {"005930": OLD, "443060": listed_later})
    assert sorted((f.code, f.context.get("ticker")) for f in found) == [
        ("NO_DATA", "443060"),
        ("NO_DATA", "999999"),
    ]
    short = {**OLD, "before_start": 50}
    spec = _spec(
        exit=_cmp({"indicator": "close", "of": "self"}, "cross_below", {"indicator": "ma", "window": 120})
    )
    assert [(f.code, f.context["need"]) for f in _existence(spec, {"005930": short})] == [("NO_DATA", 120)]
    late = {**BASE, "backtest": {**BASE["backtest"], "end": "2027-01-01"}}
    assert [f.code for f in _existence(late, {"005930": OLD})] == ["PERIOD_AFTER_DATA"]


def test_index_and_vix_subjects_warn_in_the_demo():
    spec = _spec(entry=_cmp({"indicator": "vix", "of": "VIX"}, "<", {"value": 20}))
    found = _existence(spec, {"005930": OLD})
    assert [(f.kind, f.code) for f in found] == [("warn", "SUBJECT_DATA_NOT_IN_DEMO")]


def test_logic_blocks():
    cases = {
        "단위": _spec(entry=_cmp(RSI, "<", {"indicator": "ma", "window": 5, "of": "self"})),
        "범위": _spec(entry=_cmp(RSI, ">", {"value": 150})),
        "자기 비교": _spec(
            entry=_cmp(
                {"indicator": "ma", "window": 20, "of": "self"},
                "cross_above",
                {"indicator": "ma", "window": 20, "of": "self"},
            )
        ),
        "살 때 손절": _spec(entry=_cmp({"indicator": "entry_return", "of": "self"}, ">=", {"value": 0.1})),
        "지수 거래량": _spec(
            entry=_cmp({"indicator": "volume_ratio", "window": 20, "of": "KOSPI200"}, ">", {"value": 3})
        ),
    }
    for label, spec in cases.items():
        found = chk.check_logic(spec)
        assert [(f.stage, f.kind, f.code) for f in found] == [(3, "block", "VALIDATION")], label


def test_always_both_true_is_asked():
    spec = _spec(entry=_cmp(RSI, "<", {"value": 30}), exit=_cmp(RSI, "<", {"value": 40}))
    found = chk.check_logic(spec)
    assert [(f.kind, f.code, f.path) for f in found] == [("ask", "always_both_true", "rules[0]")]
    assert found[0].context == {
        "entry": "RSI 30 밑",
        "exit": "RSI 40 밑",
        "flip_label": "RSI 40 이상이면 팔기",
    }
    # 살 범위가 팔 범위 밖으로 나가면 모순이 아니다
    assert (
        chk.check_logic(_spec(entry=_cmp(RSI, "<", {"value": 30}), exit=_cmp(RSI, ">", {"value": 70}))) == []
    )
    # "~하는 순간" 이나 묶음은 판단하지 않는다
    assert (
        chk.check_logic(
            _spec(entry=_cmp(RSI, "cross_below", {"value": 30}), exit=_cmp(RSI, "<", {"value": 40}))
        )
        == []
    )
    ret = {"indicator": "ret", "window": 20, "of": "self"}
    both = chk.check_logic(
        _spec(entry=_cmp(ret, "<=", {"value": -0.05}), exit=_cmp(ret, "<=", {"value": 0.0}))
    )
    assert both[0].context["flip_label"] == "20일 수익률 0% 위면 팔기"


def test_weight_vs_cap_is_asked():
    spec = {
        **BASE,
        "universe": {"tickers": ["005930", "000660"]},
        "weights": {"005930": 0.5, "000660": 0.5},
        "constraint": {"max_weight_per_asset": 0.4},
    }
    assert [f.code for f in chk.check_logic(spec)] == ["weight_vs_cap"]


def test_partial_spec_is_checked_where_it_exists():
    partial = {"rules": [{"target": "each", "entry": _cmp(RSI, ">", {"value": 150}), "exit": None}]}
    assert [f.code for f in chk.check_logic(partial)] == ["VALIDATION"]
