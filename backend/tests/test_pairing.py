"""AI 짝짓기와 근거 검사(app/m1/pairing.py) 테스트. 가짜 LLM 으로 돈다."""
from __future__ import annotations

import copy

from app.m1 import pairing, spans

DICTIONARY = spans.load_dictionary()
INDEX = spans.InstrumentIndex([("005930", "삼성전자"), ("069500", "KODEX 200")], DICTIONARY)
SENTENCE = "삼성전자 RSI 30 아래로 떨어지면 사고 5일선 깨지면 팔아줘"
GOOD = copy.deepcopy(pairing.EXAMPLE_OUTPUT)


class FakeClient:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.schemas: list[dict] = []

    def generate_json(self, prompt, json_schema, *, system=None):
        self.prompts.append(prompt)
        self.schemas.append(json_schema)
        return self.answers.pop(0)


def _result(text=SENTENCE):
    return spans.find_spans(text, DICTIONARY, INDEX)


def _enum(schema_part):
    return schema_part["anyOf"][1]["enum"]


def test_schema_narrows_each_field_to_span_kinds():
    schema = pairing.build_schema(_result())
    entry = schema["properties"]["rules"]["items"]["properties"]["entry"]
    item = entry["anyOf"][1]["properties"]["items"]["items"]
    assert _enum(item["properties"]["left"]) == ["2:RSI", "6:5일선"]
    assert _enum(item["properties"]["op"]) == ["4:아래로 떨어지면", "7:깨지면"]
    assert _enum(item["properties"]["right"]) == ["2:RSI", "3:30", "6:5일선"]
    assert _enum(item["properties"]["subject"]) == ["1:삼성전자"]
    assert schema["properties"]["universe"]["items"]["enum"] == ["1:삼성전자"]
    # 이 문장에 리밸런싱 조각이 없으니 빈 배열만 된다
    assert schema["properties"]["rebalance"] == {"type": "array", "maxItems": 0}


def test_prompt_lists_spans_and_example():
    prompt = pairing.build_prompt(_result())
    assert "4:아래로 떨어지면 [비교]" in prompt
    assert prompt.count("문장:") == 2
    assert "고칠 것" not in prompt


def test_good_answer_passes_without_retry():
    client = FakeClient(GOOD)
    outcome = pairing.pair(_result(), client)
    assert outcome.issues == () and not outcome.retried
    rule = outcome.pairing.rules[0]
    assert [s.text for s in rule.entry.items[0].spans()] == ["RSI", "아래로 떨어지면", "30"]
    assert rule.exit.items[0].left is None
    assert [s.text for s in outcome.pairing.universe] == ["삼성전자"]
    assert len(client.prompts) == 1


def test_unused_span_triggers_one_retry_with_feedback():
    missing_exit = copy.deepcopy(GOOD)
    missing_exit["rules"][0]["exit"] = None
    client = FakeClient(missing_exit, GOOD)
    outcome = pairing.pair(_result(), client)
    assert outcome.retried
    assert {i.span_id for i in outcome.first_issues} == {6, 7, 8}
    assert "조각 [7] '깨지면' 이 어느 칸에도 쓰이지 않았다" in client.prompts[1]
    assert outcome.issues == ()


def test_still_failing_after_retry_is_left_for_clarification():
    missing_exit = copy.deepcopy(GOOD)
    missing_exit["rules"][0]["exit"] = None
    outcome = pairing.pair(_result(), FakeClient(missing_exit, missing_exit))
    assert outcome.retried
    assert [i.code for i in outcome.issues] == ["unused_span"] * 3


def test_invented_label_and_quote_are_caught():
    bad = copy.deepcopy(GOOD)
    bad["rules"][0]["entry"]["items"][0]["right"] = "3:40"
    bad["quotes"] = [{"text": "미국 주식", "slot": "universe", "state": "unsupported"}]
    issues = pairing.evidence_issues(pairing.parse(bad, _result()), _result())
    codes = {(i.code, i.text) for i in issues}
    assert ("invalid_label", "3:40") in codes
    assert ("quote_not_in_text", "미국 주식") in codes
    assert ("unused_span", "30") in codes


def test_quoting_a_span_instead_of_placing_it_is_caught():
    dodged = copy.deepcopy(GOOD)
    dodged["rules"][0]["exit"] = None
    dodged["quotes"] = [{"text": "깨지면", "slot": "exit", "state": "ambiguous"}]
    issues = pairing.evidence_issues(pairing.parse(dodged, _result()), _result())
    codes = {(i.code, i.span_id) for i in issues}
    assert ("quoted_span", 7) in codes
    assert ("unused_span", 7) in codes


def test_quote_outside_the_span_list_is_fine():
    result = _result("삼성전자 RSI 많이 낮아지면 사")
    answer = {
        "universe": ["1:삼성전자"], "weights": [], "rebalance": [], "max_weight": None, "selection": [],
        "backtest": {"period": [], "capital": [], "dividends": []},
        "rules": [{"target": "each", "exit": None, "entry": {
            "actions": ["4:사"], "join": "one",
            "items": [{"subject": None, "left": "2:RSI", "op": "3:낮아지면", "right": None}]}}],
        "quotes": [{"text": "많이 낮아지면", "slot": "entry", "state": "ambiguous"}],
    }
    assert pairing.evidence_issues(pairing.parse(answer, result), result) == []


def test_negated_and_reject_spans_are_not_offered():
    result = _result("삼성전자랑 KODEX 200 반반, 배당은 빼고")
    prompt = pairing.build_prompt(result)
    listed = prompt.split("문장: 삼성전자랑")[1].split("조각:")[1]
    assert "배당" not in listed and "빼고" not in listed
    schema = pairing.build_schema(result)
    assert schema["properties"]["backtest"]["properties"]["dividends"] == {"type": "array", "maxItems": 0}


# ── 짝짓기 결과 정리(normalize)와 써도 되고 안 써도 되는 조각 ─────────


def _answer(text, rules, **extra):
    base = {"universe": [], "weights": [], "rebalance": [], "max_weight": None, "selection": [],
            "backtest": {"period": [], "capital": [], "dividends": []}, "quotes": [], "rules": rules}
    base.update(extra)
    result = _result(text)
    return result, pairing.normalize(pairing.parse(base, result))


def _cond(actions, items, join="one"):
    return {"actions": actions, "join": join, "items": [
        dict(zip(("subject", "left", "op", "right"), item, strict=True)) for item in items]}


def test_level_indicator_alone_moves_to_the_right():
    # "20일 이평 위면 보유, 밑이면 현금": 이평만 말하면 종가와 비교한다
    _, p = _answer("삼성전자 20일 이평 위면 보유, 밑이면 현금", [{
        "target": "each",
        "entry": _cond(["4:보유"], [(None, "2:20일 이평", "3:위면", "2:20일 이평")]),
        "exit": _cond(["6:현금"], [(None, "2:20일 이평", "5:밑이면", None)]),
    }])
    for cond in (p.rules[0].entry, p.rules[0].exit):
        assert cond.items[0].left is None
        assert cond.items[0].right.text == "20일 이평"


def test_omitted_indicator_is_inherited_from_the_other_side():
    _, p = _answer("삼성전자 RSI 30 밑이면 사고 70 넘으면 팔아", [{
        "target": "each",
        "entry": _cond(["5:사고"], [(None, "2:RSI", "4:밑이면", "3:30")]),
        "exit": _cond(["8:팔아"], [(None, None, "7:넘으면", "6:70")]),
    }])
    assert p.rules[0].exit.items[0].left.text == "RSI"


def test_two_items_with_join_one_become_all_and_empty_parts_are_dropped():
    _, p = _answer("삼성전자 RSI 30 밑이고 5일선 위면 사", [{
        "target": "each",
        "entry": _cond(["7:사"], [(None, "2:RSI", "4:밑이고", "3:30"), (None, None, "6:위면", "5:5일선"),
                                  (None, None, None, None)]),
        "exit": _cond([], [(None, None, None, None)]),
    }])
    assert p.rules[0].entry.join == "all"
    assert len(p.rules[0].entry.items) == 2
    assert p.rules[0].exit is None


def test_rules_with_the_same_target_are_merged_with_any():
    _, p = _answer("삼성전자 5일선 깨지면 팔고 RSI 70 넘으면 팔아", [
        {"target": "each", "entry": None, "exit": _cond(["4:팔고"], [(None, None, "3:깨지면", "2:5일선")])},
        {"target": "each", "entry": None, "exit": _cond(["8:팔아"], [(None, "5:RSI", "7:넘으면", "6:70")])},
    ])
    assert len(p.rules) == 1
    assert p.rules[0].exit.join == "any"
    assert [len(p.rules[0].exit.items), len(p.rules[0].exit.actions)] == [2, 2]


def test_optional_spans():
    # 비중만 말한 문장의 사기 조각, "리밸런싱" 낱말, 옆에 붙은 같은 방향 동사는 안 써도 된다
    weights = [{"asset": None, "value": "3:똑같이"}]
    result, p = _answer("삼성전자 KODEX 200 똑같이 사두고 매주 리밸런싱", [],
                        universe=["1:삼성전자", "2:KODEX 200"], weights=weights, rebalance=["5:매주"])
    assert pairing.evidence_issues(p, result) == []
    volume = "2:거래량이 20일 평균의 3배"
    result, p = _answer("삼성전자 거래량이 20일 평균의 3배 넘게 터지면 사", [{
        "target": "each", "exit": None,
        "entry": _cond(["5:사"], [(None, volume, "4:터지면", volume)])}],
        universe=["1:삼성전자"])
    assert pairing.evidence_issues(p, result) == []
    # 지표와 숫자를 같이 가진 조각이 양쪽에 있으면 왼쪽에만 둔다(숫자는 값 바꾸기가 채운다)
    item = p.rules[0].entry.items[0]
    assert (item.left.text, item.right) == ("거래량이 20일 평균의 3배", None)
