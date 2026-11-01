"""검증기 4단계: 단계마다 통과 1개 · 실패 1개."""
import copy
import json

from core.validate import validate

VALID = {
    "schema_version": "demo-0.1",
    "verdict": "ok",
    "questions": [],
    "reject_reason": None,
    "universe": [{"ticker": "005930", "name": "삼성전자", "weight": 1.0}],
    "entry": {"logic": "all", "conditions": [{"left": "RSI_14", "op": "cross_below", "right": 30}]},
    "exit": {
        "logic": "any",
        "conditions": [{"left": "CLOSE", "op": "cross_below", "right": "SMA_5"}],
        "stop_loss": None, "take_profit": None, "max_hold_days": None,
    },
    "backtest": {"start": "2021-01-01", "end": "2025-12-31", "initial_cash": 10_000_000},
    "costs": {"commission_bps": 1.5, "tax_profile": "kr_default"},
    "provenance": {
        "input_text": "RSI 30 아래로 떨어지면 사고, 5일선 깨지면 팔아줘",
        "mappings": [
            {"phrase": "RSI 30 아래로 떨어지면", "field": "entry.conditions[0]"},
            {"phrase": "5일선 깨지면", "field": "exit.conditions[0]"},
        ],
        "defaults_applied": ["universe", "backtest", "costs"],
    },
}


def spec(**over) -> dict:
    d = copy.deepcopy(VALID)
    d.update(copy.deepcopy(over))
    return d


def status(report) -> list[str]:
    return [s.status for s in report.stages]


# ---------------------------------------------------------------- 1. 형식

def test_stage1_pass_json_string():
    r = validate(json.dumps(VALID, ensure_ascii=False))
    assert r.stages[0].status == "passed"
    assert r.ok


def test_stage1_fail_unknown_indicator():
    d = spec(entry={"logic": "all", "conditions": [{"left": "MACD", "op": "gt", "right": 0}]})
    r = validate(d)
    assert status(r) == ["failed", "skipped", "skipped", "skipped"]
    (issue,) = r.stages[0].issues
    assert issue.stage == 1 and issue.stage_name == "형식"
    assert issue.field == "entry.conditions[0].left"
    assert "허용되지 않은 값" in issue.message and "MACD" in issue.message
    assert r.spec is None


# ---------------------------------------------------------------- 2. 참조

def test_stage2_pass_two_tickers_and_volume_multiple():
    d = spec(
        universe=[{"ticker": "005930", "name": "삼성전자", "weight": 0.5},
                  {"ticker": "069500", "name": "KODEX 200", "weight": 0.5}],
        entry={"logic": "all", "conditions": [
            {"left": "VOLUME", "op": "gt", "right": {"ref": "VOL_SMA_20", "mul": 2}}]},
    )
    r = validate(d)
    assert r.stages[1].status == "passed"
    assert r.ok


def test_stage2_fail_ticker_not_in_dictionary():
    d = spec(universe=[{"ticker": "999999", "name": "없는종목", "weight": 1.0}])
    r = validate(d)
    assert status(r) == ["passed", "failed", "skipped", "skipped"]
    (issue,) = r.stages[1].issues
    assert issue.field == "universe[0].ticker"
    assert "종목 사전" in issue.message and "999999" in issue.message


# ---------------------------------------------------------------- 3. 논리

def test_stage3_pass_exit_by_stop_loss_only():
    d = spec(exit={"logic": "any", "conditions": [], "stop_loss": 0.05, "take_profit": None, "max_hold_days": None})
    d["provenance"]["mappings"] = d["provenance"]["mappings"][:1]   # exit.conditions[0] 이 사라졌으므로
    r = validate(d)
    assert r.stages[2].status == "passed"
    assert r.ok


def test_stage3_fail_no_exit_rule():
    d = spec(exit={"logic": "any", "conditions": [], "stop_loss": None, "take_profit": None, "max_hold_days": None})
    d["provenance"] = None
    r = validate(d)
    assert status(r) == ["passed", "passed", "failed", "skipped"]
    (issue,) = r.stages[2].issues
    assert issue.field == "exit"
    assert "매도 조건·손절·익절·보유기간" in issue.message


# ---------------------------------------------------------------- 4. 범위

def test_stage4_pass_boundary_values():
    d = spec(
        universe=[{"ticker": "005930", "name": "삼성전자", "weight": 0.4},
                  {"ticker": "000660", "name": "SK하이닉스", "weight": 0.3},
                  {"ticker": "069500", "name": "KODEX 200", "weight": 0.3}],
        exit={"logic": "any", "conditions": [{"left": "CLOSE", "op": "cross_below", "right": "SMA_5"}],
              "stop_loss": 0.5, "take_profit": 0.5, "max_hold_days": 250},
    )
    r = validate(d)
    assert status(r) == ["passed"] * 4


def test_stage4_fail_stop_loss_as_percent():
    d = spec(exit={"logic": "any", "conditions": [{"left": "CLOSE", "op": "cross_below", "right": "SMA_5"}],
                   "stop_loss": 5, "take_profit": None, "max_hold_days": None})
    r = validate(d)
    assert status(r) == ["passed", "passed", "passed", "failed"]
    (issue,) = r.stages[3].issues
    assert issue.stage_name == "범위"
    assert issue.field == "exit.stop_loss"
    assert "0.05 = 5%" in issue.message
    assert str(issue).startswith("[4단계 범위] exit.stop_loss:")
