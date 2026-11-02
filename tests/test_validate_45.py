"""검사 4단계(근거) · 5단계(시험 실행) · 2단계 NO_DATA · 단계 끄기(Stages) · 연산자 규칙(A) · 반반(C).

'일부러 틀린 전략서': 규칙으로 조립한 정상 전략서를 만든 뒤 값을 하나씩 바꿔 넣고, 어느 단계에서 잡히는지 본다.
근거(LLM 이 적은 원문 구절)는 그대로 두고 전략서 값만 바꾸므로 'LLM 이 근거와 다른 값을 적은' 경우와 같다.
"""
import copy

import pytest

from conftest import cmp as scmp
from conftest import ind, make_ohlcv, wavy_ohlcv
from core.assemble import assemble, grounding
from core.decide import decide, op_from_table
from core.pipeline import run
from core.validate import Stages, required_history, validate
from slots import MA5_BREAK, RSI30_CROSS, SAMSUNG, asset, assets, basic, cmp, cond, fill, op, rt, val

TEXT = "삼성전자 RSI 30 아래로 떨어지면 사고, 5일선 깨지면 팔아줘"


def build(slots=None, text=TEXT, answers=None):
    """빈칸 → 판정 → 조립. (전략서 dict, 근거)"""
    d = decide(slots or basic(), answers, text=text)
    assert d.verdict == "proceed", (d.verdict, d.rejection, [q.text for q in d.questions])
    return assemble(d.resolved), grounding(d.resolved, text, answers)


def codes(rep, stage=None):
    return [(i.stage, i.code) for i in rep.issues if stage is None or i.stage == stage]


def stage(rep, n):
    return rep.stages[n - 1]


# ---------------------------------------------------------------- A. 연산자는 표현 표가 정한다

@pytest.mark.parametrize("evidence,llm_op,expected", [
    ("RSI 30 아래로 떨어지면", "<", "cross_below"),
    ("RSI 30 아래로 떨어지면", None, "cross_below"),
    ("RSI 70 넘으면", ">", "cross_above"),
    ("RSI 70 넘으면", ">=", "cross_above"),
])
def test_op_from_direction_verb_overrides_llm(evidence, llm_op, expected):
    side = "exit" if "70" in evidence else "entry"
    v = 70 if "70" in evidence else 30
    s = basic(**{side: cond(cmp(evidence, op("rsi", 14, "self"), llm_op, val(v)))})
    d = decide(s, text=TEXT)
    c = getattr(d.resolved, side).items[0]
    assert c.op == expected and c.op_source == "direction"
    logged = [x for x in d.op_log if x["compare"] == f"{side}[0]"]
    if llm_op is not None and llm_op != expected:
        assert logged == [{"compare": f"{side}[0]", "evidence": evidence, "llm_op": llm_op, "table_op": expected,
                           "source": "direction"}]          # LLM 값과 다르면 기록만
    else:
        assert logged == []


def test_op_phrase_beats_bare_verb():
    """'10% 손실이면'·'{p}% 빠지면 팔아' 같은 조건 구절이 동사 구절보다 구체적이라 먼저."""
    assert op_from_table("10% 빠지면 팔아") == ("<=", "phrase")     # 동사만 보면 '빠지면' = cross_below
    assert op_from_table("5일선 깨지면") == ("cross_below", "phrase")
    assert op_from_table("10% 떨어지면") == ("cross_below", "direction")
    assert op_from_table("20일선 위로 올라가면") == ("cross_above", "direction")     # 2026-11-02 추가
    assert op_from_table("20일선 위에 있을 때") == (None, "none")


@pytest.mark.parametrize("text,evidence,expected", [
    ("SK하이닉스 20일선 위로 올라가면 사고 10% 떨어지면 손절해줘", "10% 떨어지면", "<="),
    ("카카오 60일선 돌파하면 사고 15% 오르면 익절, 7% 떨어지면 손절", "15% 오르면", ">="),
    ("카카오 60일선 돌파하면 사고 15% 오르면 익절, 7% 떨어지면 손절", "7% 떨어지면", "<="),
    ("삼성전자에 70%만 넣고 RSI 30 아래면 사고 20거래일 지나면 팔아", "20거래일 지나면 팔아", ">="),
])
def test_stop_loss_take_profit_phrases_beat_verbs(text, evidence, expected):
    """손절·익절 구절이 동사 구절('떨어지면' = cross_below)보다 먼저. LLM 이 근거를 '10% 떨어지면'까지만 적어도
    원문에서 바로 뒤 낱말('손절해줘')까지 보고 구절을 찾는다."""
    from core.decide import with_next_word
    assert op_from_table(evidence, with_next_word(evidence, text)) == (expected, "phrase")


def test_op_without_verb_uses_llm_and_is_grounding_target():
    s = basic(entry=cond(cmp("20일선 위에 있을 때", op("close", None, "self"), ">", rt("ma", 20, "self"))))
    text = "삼성전자 20일선 위에 있을 때 사고, 5일선 깨지면 팔아줘"
    d = decide(s, text=text)
    assert d.resolved.entry.items[0].op == ">" and d.resolved.entry.items[0].op_source == "llm"
    spec, g = build(s, text)
    rep = validate(spec, grounding=g)
    assert codes(rep, 4) == [(4, "UNGROUNDED_OP")]
    q = rep.issues[0]
    assert q.options == ["네", "문장을 고쳐서 다시 입력"] and "20일 이동평균 위" in q.question


# ---------------------------------------------------------------- C. 반반 · 정량 정의 없는 말

def test_half_with_one_asset_asks_other_ticker():
    """LLM 이 '삼성전자 반반'을 같은 종목 두 개로 적음 → 되묻기 (3단계 DUPLICATE 막힘으로 가지 않는다)."""
    s = basic(assets={"status": "filled", "evidence": "삼성전자 반반으로", "reason": "",
                      "value": [asset("삼성전자", weight=50), asset("삼성전자", weight=50)]})
    text = "삼성전자 반반으로 RSI 30 아래로 떨어지면 사고 5일선 깨지면 팔아"
    d = decide(s, text=text)
    assert d.verdict == "clarify" and [q.id for q in d.questions] == ["assets:half"]
    assert d.questions[0].text == "반반으로 나눌 다른 종목은 무엇인가요?"
    d = decide(s, [{"assets:half": "SK하이닉스"}], text=text)
    assert d.verdict == "proceed"
    assert [(a["name"], a["weight"]) for a in d.resolved.assets] == [("삼성전자", 0.5), ("SK하이닉스", 0.5)]
    spec, g = build(s, text, [{"assets:half": "SK하이닉스"}])
    assert validate(spec, grounding=g).verdict == "ok"      # 새 종목의 근거는 답


def test_half_with_two_assets_is_fine():
    s = basic(assets={"status": "filled", "evidence": "현대차랑 기아 반반", "reason": "",
                      "value": [asset("현대차", weight=50), asset("기아", weight=50)]})
    assert decide(s, text="현대차랑 기아 반반, RSI 30 아래로 떨어지면 사고 5일선 깨지면 팔아").verdict == "proceed"


@pytest.mark.parametrize("evidence", ["세력 들어오면", "바닥에서 사서", "고점에서 팔아"])
def test_words_without_quantitative_definition_reject(evidence):
    s = basic(entry=cond(cmp(evidence, op(), None, rt(), status="ambiguous")))
    d = decide(s, text=evidence)
    assert d.verdict == "reject" and d.rejection.code == "UNKNOWN_INDICATOR"


# ---------------------------------------------------------------- 4단계: 일부러 틀린 전략서

def test_clean_spec_passes_grounding():
    spec, g = build()
    rep = validate(spec, grounding=g)
    assert rep.verdict == "ok" and stage(rep, 4).status == "passed", [str(i) for i in rep.issues]


def _mutated(mutate, slots=None, text=TEXT):
    spec, g = build(slots, text)
    spec = copy.deepcopy(spec)
    mutate(spec)
    return validate(spec, grounding=g)


@pytest.mark.parametrize("name,mutate,expect", [
    ("지어낸 종목 (근거는 삼성전자)",
     lambda d: d["assets"][0].update(ticker="000660", name="SK하이닉스"), ("VALUE_MISMATCH", "assets[0].ticker")),
    ("근거 없이 끼워 넣은 종목",
     lambda d: d["assets"].append({"ticker": "069500", "name": "KODEX 200", "weight": None}),
     ("NO_EVIDENCE", "assets[1]")),
    ("숫자 30 → 20",
     lambda d: d["rules"]["entry"]["right"].update(value=20), ("VALUE_MISMATCH", "rules.entry.right.value")),
    ("'5일선' 근거에 20일 값",
     lambda d: d["rules"]["exit"]["right"].update(window=20), ("VALUE_MISMATCH", "rules.exit")),
    ("동사와 다른 연산자 ('떨어지면'인데 <)",
     lambda d: d["rules"]["entry"].update(op="<"), ("VALUE_MISMATCH", "rules.entry")),
    ("코스피 값으로 바꿔치기",
     lambda d: d["rules"]["entry"]["left"].update(of="KOSPI200"), ("VALUE_MISMATCH", "rules.entry.left.of")),
    ("말하지 않은 시작 금액",
     lambda d: (d["backtest"].update(initial_cash=50_000_000), d["defaults_applied"].remove("backtest.initial_cash")),
     ("NO_EVIDENCE", "backtest.initial_cash")),
])
def test_grounding_catches_fabricated_values(name, mutate, expect):
    rep = _mutated(mutate)
    assert rep.verdict == "clarify" and stage(rep, 4).status == "questions", name
    found = [(i.code, i.field) for i in rep.issues if i.stage == 4]
    assert any(c == expect[0] and f.startswith(expect[1]) for c, f in found), (name, found)


def test_default_phrase_compare_is_checked_against_phrase():
    """'과매도' = RSI 30 (기본값 구절). 전략서 값이 구절과 다르면 걸린다."""
    s = basic(entry=cond(cmp("과매도면", op("rsi", 14, "self"), "<", val(30))))
    text = "삼성전자 과매도면 사고, 5일선 깨지면 팔아줘"
    spec, g = build(s, text)
    assert "rules.entry" in spec["defaults_applied"]
    assert validate(spec, grounding=g).verdict == "ok"
    spec["rules"]["entry"]["right"]["value"] = 20
    assert ("VALUE_MISMATCH", "rules.entry") in [(i.code, i.field) for i in validate(spec, grounding=g).issues]


def test_number_changed_is_also_caught_by_number_check():
    rep = _mutated(lambda d: d["rules"]["entry"]["right"].update(value=20))
    msgs = [i.message for i in rep.issues if i.code == "NUMBER_MISSING"]
    assert msgs == ["원문의 숫자 30이 전략서에 없습니다."]
    rep = _mutated(lambda d: d["rules"]["exit"]["right"].update(window=20))
    assert "원문의 숫자 5가 전략서에 없습니다." in [i.message for i in rep.issues]


def test_evidence_not_in_text():
    """LLM 이 원문에 없는 구절을 근거로 적음."""
    s = basic(assets=assets(asset("KODEX 200")))
    rep = validate_pair(s, TEXT)
    assert ("EVIDENCE_NOT_IN_TEXT", "assets[0]") in [(i.code, i.field) for i in rep.issues]
    # 원문의 '삼성전자'는 전략서에 없으니 빠뜨림으로도 잡힌다
    assert "원문의 종목 '삼성전자'가 전략서에 없습니다." in [i.message for i in rep.issues]


def validate_pair(slots, text, **kw):
    spec, g = build(slots, text)
    return validate(spec, grounding=g, **kw)


def test_omitted_country_us():
    """데모 5번 샘플: '미국'이 어느 빈칸에도 안 쓰임 → 빠뜨림."""
    text = "삼성전자랑 미국도 좀 섞어서 RSI 30 아래로 떨어지면 사고, 5일선 깨지면 팔아줘"
    rep = validate_pair(basic(), text)
    om = [i for i in rep.issues if i.code == "OMITTED"]
    assert [i.message for i in om] == ["원문의 '미국'이 어느 빈칸의 근거로도 쓰이지 않았습니다."]
    assert om[0].question == "'미국'을 어떻게 반영할까요?" and om[0].options == ["무시하고 진행", "문장을 고쳐서 다시 입력"]


def test_omitted_ticker_and_index_and_sell():
    text = "삼성전자랑 SK하이닉스, 코스피가 RSI 30 아래로 떨어지면 사고, 5일선 깨지면 팔아줘"
    rep = validate_pair(basic(), text)
    msgs = [i.message for i in rep.issues if i.code == "OMITTED"]
    assert "원문의 종목 'SK하이닉스'가 전략서에 없습니다." in msgs
    assert "원문의 '코스피가'가 어느 빈칸의 근거로도 쓰이지 않았습니다." in msgs
    s = basic(exit=cond())                   # '팔아' 가 있는데 팔 조건이 없음 ('계속 갖고 있기' 답 없이)
    spec, g = build(s, TEXT, [{"exit:none": "계속 갖고 있기"}])
    g.hold = False
    assert "원문에 파는 말('팔아')이 있는데 팔 때 조건이 없습니다." in [i.message for i in validate(spec, grounding=g).issues]


def test_numbers_inside_ticker_names_are_not_counted():
    s = basic(assets=assets(asset("KODEX 200")))
    text = "KODEX 200 RSI 30 아래로 떨어지면 사고, 5일선 깨지면 팔아줘"
    assert validate_pair(s, text).verdict == "ok"


def test_answers_are_evidence():
    """되묻기 답으로 바뀐 값은 답이 근거다 (7일선 → 5일선 답: 원문의 7 은 숫자 검사에서 빼고, 5 는 답에서 왔다)."""
    s = basic(exit=cond(cmp("7일선 깨지면", op("close", None, "self"), "cross_below", rt("ma", 7, "self"),
                            status="unsupported", kind="bad_window")))
    text = "삼성전자 RSI 30 아래로 떨어지면 사고, 7일선 깨지면 팔아줘"
    answers = [{"exit[0]:window": "5일선"}]
    spec, g = build(s, text, answers)
    assert spec["rules"]["exit"]["right"]["window"] == 5
    assert validate(spec, grounding=g).verdict == "ok"


# ---------------------------------------------------------------- 3단계에서 먼저 막히는 틀린 전략서

@pytest.mark.parametrize("name,entry,code", [
    ("RSI 150", scmp(ind("rsi", 14), "cross_above", 150), "OUT_OF_RANGE"),
    ("RSI vs 5일선", scmp(ind("rsi", 14), "<", ind("ma", 5)), "UNIT_MISMATCH"),
    ("산 가격 대비로 사기", scmp(ind("entry_return"), ">=", 10), "EXIT_ONLY_IN_ENTRY"),
])
def test_logic_errors_block_before_grounding(name, entry, code):
    rep = _mutated(lambda d: d["rules"].update(entry=entry))
    assert rep.verdict == "reject" and rep.failed_stage == 3 and (3, code) in codes(rep), name
    assert stage(rep, 4).status == "skipped" and stage(rep, 4).note == "앞 단계에서 막힘"


# ---------------------------------------------------------------- 단계 끄기

def test_stages_off_lets_mutation_through():
    bad = lambda d: d["rules"]["exit"]["right"].update(window=20)   # noqa: E731
    on = _mutated(bad)
    spec, g = build()
    spec["rules"]["exit"]["right"]["window"] = 20
    off = validate(spec, grounding=g, stages=Stages.ALL & ~Stages.GROUNDING)
    assert on.verdict == "clarify" and off.verdict == "ok"
    assert (stage(off, 4).status, stage(off, 4).note) == ("skipped", "꺼짐")
    only_shape = validate(spec, stages=Stages.SHAPE)
    assert [s.status for s in only_shape.stages] == ["passed", "skipped", "skipped", "skipped", "skipped"]


# ---------------------------------------------------------------- 2단계 NO_DATA

def _spec_dates(start, end, rules=None):
    spec, _ = build()
    spec["backtest"].update(start=start, end=end)
    if rules is not None:
        spec["rules"] = rules
    return spec


def test_required_history_counts_lookback():
    spec = _spec_dates("2024-06-03", "2024-12-31", {"target": "each", "entry": scmp(ind("close"), "cross_above", ind("ma", 120)),
                                                    "exit": scmp(ind("rsi", 14), ">", 70)})
    need = validate(spec).spec
    assert required_history(need) == {"005930": (120, "120일 이동평균 (~하는 순간: 전날 값 포함)")}


def test_no_data_blocks_when_history_too_short():
    df = wavy_ohlcv(150)                                 # 2024-01-02 부터 150거래일
    spec = _spec_dates("2024-03-04", "2024-07-31", {"target": "each",
                                                    "entry": scmp(ind("close"), "cross_above", ind("ma", 120)),
                                                    "exit": scmp(ind("rsi", 14), ">", 70)})
    rep = validate(spec, prices={"005930": df}, stages=Stages.ALL & ~Stages.TRIAL)
    assert rep.verdict == "reject" and rep.failed_stage == 2 and (2, "NO_DATA") in codes(rep)
    assert "앞 120거래일이 필요한데" in rep.issues[0].message
    ok = _spec_dates("2024-07-01", "2024-07-31", {"target": "each", "entry": scmp(ind("close"), "cross_above", ind("ma", 120)),
                                                  "exit": scmp(ind("rsi", 14), ">", 70)})
    assert validate(ok, prices={"005930": df}, stages=Stages.ALL & ~Stages.TRIAL).verdict == "ok"


def test_no_data_for_listing_after_start_and_missing_symbol():
    df = wavy_ohlcv(100)
    rep = validate(_spec_dates("2023-01-02", "2024-03-29"), prices={"005930": df}, stages=Stages.EXIST)
    assert (2, "NO_DATA") in codes(rep)
    rep = validate(_spec_dates("2024-06-03", "2024-12-31"), prices={}, stages=Stages.EXIST)
    assert rep.issues[0].message == "005930 가격 데이터가 없습니다."


def test_no_data_index_symbol():
    spec = _spec_dates("2024-06-03", "2024-12-31", {"target": "each",
                                                    "entry": scmp(ind("close", of="KOSPI200"), "<", ind("ma", 60, of="KOSPI200")),
                                                    "exit": scmp(ind("rsi", 14), ">", 70)})
    rep = validate(spec, prices={"005930": wavy_ohlcv(300), "KS200": wavy_ohlcv(300).iloc[80:]}, stages=Stages.EXIST)
    assert [i.field for i in rep.issues] == ["KS200"]


# ---------------------------------------------------------------- 5단계 시험 실행 (경고만)

DATA = {"005930": wavy_ohlcv(400)}


def _trial(entry, exit_):
    spec = _spec_dates("2024-06-03", "2025-06-30", {"target": "each", "entry": entry, "exit": exit_})
    return validate(spec, prices=DATA)


def test_trial_never_buys_warns():
    rep = _trial(scmp(ind("rsi", 14), "<", 1), scmp(ind("rsi", 14), ">", 70))
    assert rep.verdict == "ok" and stage(rep, 5).status == "warned"
    assert codes(rep, 5) == [(5, "NEVER_BUY")] and rep.issues[0].message == "이 기간에 한 번도 사지 않습니다."
    assert rep.trial is not None


def test_trial_too_frequent_warns():
    rep = _trial(scmp(ind("rsi", 14), "<", 100), scmp(ind("holding_days"), ">=", 1))
    assert (5, "TOO_FREQUENT") in codes(rep) and rep.verdict == "ok"
    assert "수수료·세금" in next(i.message for i in rep.issues if i.code == "TOO_FREQUENT")


def test_trial_same_day_warns():
    rep = _trial(scmp(ind("close"), ">", ind("ma", 5)), scmp(ind("close"), ">", ind("ma", 20)))
    assert (5, "SAME_DAY") in codes(rep) and rep.verdict == "ok"


def test_trial_normal_strategy_no_warning():
    rep = _trial(scmp(ind("rsi", 14), "cross_below", 40), scmp(ind("close"), "cross_below", ind("ma", 5)))
    assert stage(rep, 5).status == "passed", [str(i) for i in rep.issues]


# ---------------------------------------------------------------- 파이프라인: 걸리면 한 번 다시 채우기 → 그래도 걸리면 되묻기

def fake_chat(*outputs):
    calls = []

    def chat(messages, schema):
        calls.append(messages)
        out = outputs[min(len(calls) - 1, len(outputs) - 1)]
        return {"message": {"content": out.model_dump_json()}, "prompt_eval_count": 100, "eval_count": 10}
    chat.calls = calls
    return chat


BAD = basic(exit=cond(cmp("5일선 깨지면", op("close", None, "self"), "cross_below", rt("ma", 20, "self"))))


def test_pipeline_retries_once_then_proceeds(tmp_path):
    chat = fake_chat(BAD, basic())
    r = run(TEXT, chat_fn=chat, cache_dir=tmp_path)
    assert r.verdict == "proceed" and len(chat.calls) == 2
    assert [i.code for i in r.retry["issues"]] == ["VALUE_MISMATCH", "NUMBER_MISSING"]
    feedback = chat.calls[1][-1]["content"]
    assert "[검사 메모 시작]" in feedback and "'5일선 깨지면'" in feedback and "20일 이동평균" in feedback
    assert "메모 문구를 reason·kind·값에 옮겨 적지 마라" in feedback      # 메모를 값으로 착각하지 않게
    assert r.retry["kept_first"] is None
    assert chat.calls[1][-2]["role"] == "assistant"          # 첫 출력을 보여 주고 다시 채우게 한다


def test_pipeline_keeps_first_fill_when_retry_adds_unsupported(tmp_path):
    """ok-08 같은 경우: 다시 채우기가 사전에 있는 지표를 '없는 지표'(unsupported)로 바꿈 → 버리고 처음 결과로 되묻기."""
    worse = basic(exit=cond(cmp("5일선 깨지면", op("close", None, "self"), "cross_below", rt("ma", 5, "self"),
                                status="unsupported", kind="unknown_indicator",
                                reason="20일 이동평균에 해당하는 말이 없음")))
    chat = fake_chat(BAD, worse)
    r = run(TEXT, chat_fn=chat, cache_dir=tmp_path)
    assert len(chat.calls) == 2 and r.verdict == "clarify"            # 거절(UNKNOWN_INDICATOR)로 가지 않는다
    assert r.retry["kept_first"] == "다시 채우기에서 새 못 다룸: 5일선 깨지면"
    assert r.fill is r.retry["first_fill"]
    assert r.questions[0].id.startswith("validate:VALUE_MISMATCH:rules.exit")


def test_pipeline_asks_when_retry_still_fails(tmp_path):
    chat = fake_chat(BAD, BAD)
    r = run(TEXT, chat_fn=chat, cache_dir=tmp_path)
    assert r.verdict == "clarify" and len(chat.calls) == 2
    assert r.questions[0].id.startswith("validate:VALUE_MISMATCH:rules.exit")


def test_pipeline_ignore_omitted_word(tmp_path):
    text = "삼성전자랑 미국도 좀 섞어서 RSI 30 아래로 떨어지면 사고, 5일선 깨지면 팔아줘"
    chat = fake_chat(basic())
    r = run(text, chat_fn=chat, cache_dir=tmp_path)
    assert r.verdict == "clarify" and [q.text for q in r.questions] == ["'미국'을 어떻게 반영할까요?"]
    r2 = run(text, [{r.questions[0].id: "무시하고 진행"}], chat_fn=chat, cache_dir=tmp_path)
    assert r2.verdict == "proceed" and len(chat.calls) == 2        # 다시 채우기는 캐시
    off = run(text, chat_fn=fake_chat(basic()), cache_dir=tmp_path / "off", stages=Stages.ALL & ~Stages.GROUNDING)
    assert off.verdict == "proceed" and off.retry is None
