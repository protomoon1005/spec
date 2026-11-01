"""검증기 4단계: 단계마다 통과 1개 · 실패 1개 + 탈출구·원문 대조·최종 판정."""
import copy
import json

from legacy.validate import validate

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
        "input_text": "삼성전자 RSI 30 아래로 떨어지면 사고, 5일선 깨지면 팔아줘",
        "mappings": [
            {"phrase": "삼성전자", "field": "universe[0]"},
            {"phrase": "RSI 30 아래로 떨어지면", "field": "entry.conditions[0]"},
            {"phrase": "5일선 깨지면", "field": "exit.conditions[0]"},
        ],
        "defaults_applied": ["backtest", "costs"],
    },
    "unmapped_phrases": [],
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
        provenance=None,
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
    d["provenance"]["mappings"] = d["provenance"]["mappings"][:2]   # exit.conditions[0] 이 사라졌으므로
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
    # 판정 원칙: 매도 조건 없음은 reject 가 아니라 '언제 팔까요?' clarify
    assert issue.kind == "clarify" and r.verdict == "clarify"
    assert r.questions and r.questions[0].startswith("언제 팔까요") and r.questions[0].endswith("?")


# ---------------------------------------------------------------- 4. 범위

def test_stage4_pass_boundary_values():
    d = spec(
        universe=[{"ticker": "005930", "name": "삼성전자", "weight": 0.4},
                  {"ticker": "000660", "name": "SK하이닉스", "weight": 0.3},
                  {"ticker": "069500", "name": "KODEX 200", "weight": 0.3}],
        exit={"logic": "any", "conditions": [{"left": "CLOSE", "op": "cross_below", "right": "SMA_5"}],
              "stop_loss": 0.5, "take_profit": 0.5, "max_hold_days": 250},
        provenance=None,
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


# ---------------------------------------------------------------- 탈출구 (UNKNOWN · UNSUPPORTED · unmapped_phrases)

def test_unknown_ticker_turns_ok_into_clarify():
    d = spec(universe=[{"ticker": "UNKNOWN", "name": "UNKNOWN", "weight": 1.0}], unmapped_phrases=["에코프로"])
    d["provenance"]["input_text"] = "에코프로 RSI 30 아래로 떨어지면 사고, 5일선 깨지면 팔아줘"
    d["provenance"]["mappings"][0]["phrase"] = "에코프로"
    r = validate(d)
    assert d["verdict"] == "ok" and r.verdict == "clarify" and r.overridden and not r.ok
    assert status(r) == ["passed", "failed", "skipped", "skipped"]
    assert r.questions and all(q.endswith("?") for q in r.questions)


def test_unsupported_indicator_turns_ok_into_clarify():
    d = spec(entry={"logic": "all", "conditions": [{"left": "UNSUPPORTED", "op": "lt", "right": 30}]},
             unmapped_phrases=["RSI 9일"])
    d["provenance"]["input_text"] = "삼성전자 RSI 9일 30 아래로 떨어지면 사고, 5일선 깨지면 팔아줘"
    d["provenance"]["mappings"] = [m for m in d["provenance"]["mappings"] if m["field"] != "entry.conditions[0]"]
    r = validate(d)
    assert r.verdict == "clarify"
    assert any("RSI 9일" in q for q in r.questions)


def test_unmapped_fundamentals_phrase_is_reject():
    r = validate(spec(unmapped_phrases=["PER 10 아래면"]))
    assert r.verdict == "reject"
    assert r.reject_reason.code == "UNSUPPORTED_CONCEPT" and "재무지표" in r.reject_reason.detail


def test_unmapped_intraday_overrides_llm_clarify_to_reject():
    d = spec(verdict="clarify", questions=["몇 분봉으로 할까요?"], universe=[], entry=None, exit=None,
             unmapped_phrases=["5분봉"], provenance=None)
    r = validate(d)
    assert r.verdict == "reject" and r.overridden


def test_llm_reject_stays_reject_even_with_clarify_issues():
    d = spec(verdict="reject", reject_reason={"code": "UNSUPPORTED_ACTION", "detail": "공매도 불가"},
             universe=[{"ticker": "UNKNOWN", "name": "UNKNOWN", "weight": 1.0}], entry=None, exit=None,
             provenance=None)
    r = validate(d)
    assert r.verdict == "reject" and r.reject_reason.code == "UNSUPPORTED_ACTION"


# ---------------------------------------------------------------- 원문 대조

def test_ticker_not_in_source_is_clarify_not_silent_swap():
    """문장은 KB금융인데 명세는 TIGER 200 (3단계 eval ok-11 에서 실제로 나온 바꿔치기)."""
    d = spec(universe=[{"ticker": "102110", "name": "TIGER 200", "weight": 1.0}])
    d["provenance"]["input_text"] = "KB금융 RSI 30 아래로 떨어지면 사고, 5일선 깨지면 팔아줘"
    d["provenance"]["mappings"] = d["provenance"]["mappings"][1:]
    r = validate(d)
    assert r.verdict == "clarify"
    (issue,) = r.stages[1].issues
    assert issue.field == "universe[0]" and "TIGER 200" in issue.message


def test_ticker_alias_in_source_passes():
    d = spec(universe=[{"ticker": "000660", "name": "SK하이닉스", "weight": 1.0}])
    d["provenance"]["input_text"] = "하이닉스 RSI 30 아래로 떨어지면 사고, 5일선 깨지면 팔아줘"
    d["provenance"]["mappings"][0]["phrase"] = "하이닉스"
    assert validate(d).ok


def test_default_ticker_without_mention_is_clarify():
    d = spec()
    d["provenance"]["input_text"] = "RSI 30 아래로 떨어지면 사고, 5일선 깨지면 팔아줘"
    d["provenance"]["mappings"] = d["provenance"]["mappings"][1:]
    r = validate(d)
    assert r.verdict == "clarify" and r.questions[0].startswith("어떤 종목")


def test_provenance_phrase_not_in_source_is_invalid():
    d = spec()
    d["provenance"]["mappings"][1]["phrase"] = "RSI가 30 밑으로 내려가면"     # 원문은 'RSI 30 아래로 떨어지면'
    r = validate(d)
    assert r.verdict == "invalid"
    assert any(i.field == "provenance.mappings[1].phrase" for i in r.issues)


def test_explicit_source_text_overrides_provenance():
    """LLM 이 provenance 를 비워도 원문 대조가 생략되지 않는다."""
    d = spec(provenance=None)
    assert validate(d).ok                                  # 수기 명세: 원문 없음 → 대조 생략
    assert validate(d, source_text="RSI 30 아래로 떨어지면 사고 5일선 깨지면 팔아").verdict == "clarify"


def test_source_text_excludes_question_text():
    from legacy.validate import source_text_of
    composed = ("RSI 30 아래면 사\n\n[추가 질문에 대한 사용자 답변]\n"
                "- 질문: 어떤 종목으로 할까요 (예: 삼성전자, KODEX 200)?\n  답변: 카카오\n"
                "- 질문: 언제 팔까요?\n  답변: 5일선 깨지면")
    src = source_text_of(composed)
    assert "카카오" in src and "5일선 깨지면" in src and "삼성전자" not in src
    nested = composed + "\n\n[추가 질문에 대한 사용자 답변]\n- 질문: 비중은요 (예: 삼성전자)?\n  답변: 전부"
    assert source_text_of(nested).split("\n") == ["RSI 30 아래면 사", "카카오", "5일선 깨지면", "전부"]


# ---------------------------------------------------------------- clarify 질문 형식 (1단계)

def test_clarify_question_must_end_with_question_mark():
    d = spec(verdict="clarify", questions=["RSI 30 아래로 떨어지면"], provenance=None)
    r = validate(d)
    assert r.verdict == "invalid" and r.failed_stage == 1
    assert r.issues[0].field == "questions" and "물음표" in r.issues[0].message


def test_clarify_without_questions_is_invalid():
    r = validate(spec(verdict="clarify", questions=[], provenance=None))
    assert r.verdict == "invalid" and r.failed_stage == 1


def test_clarify_question_with_example_before_question_mark_passes():
    d = spec(verdict="clarify", questions=["얼마나 빠졌을 때 살까요 (예: RSI 30 아래)?"], provenance=None)
    r = validate(d)
    assert r.verdict == "clarify" and r.questions == d["questions"]
