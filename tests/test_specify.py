"""명세화 단계 — LLM 없이 가짜 chat_fn 으로 흐름만 검사한다."""
import json
from pathlib import Path

import pytest

from core.specify import (EXAMPLES, build_system_prompt, compose_refined_input, refine, specify)
from core.dictionary import INDICATORS, load_tickers

EVAL_PATH = Path(__file__).resolve().parents[1] / "eval" / "dev_sentences.jsonl"
EVAL = [json.loads(l) for l in EVAL_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]


def fake_chat(*outputs):
    """호출될 때마다 outputs 를 차례로 돌려주는 가짜 Ollama. 받은 messages 를 기록한다."""
    calls = []

    def chat(messages, schema):
        calls.append(messages)
        out = outputs[len(calls) - 1]
        return {"message": {"content": out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)},
                "prompt_eval_count": 100, "eval_count": 10}
    chat.calls = calls
    return chat


@pytest.mark.parametrize("text,out", EXAMPLES, ids=[e[1]["verdict"] + str(i) for i, e in enumerate(EXAMPLES)])
def test_prompt_examples_pass_validator(text, out, tmp_path):
    r = specify(text, chat_fn=fake_chat(out), cache_dir=tmp_path)
    assert r.next_step == out["verdict"].replace("ok", "confirm"), [str(i) for i in r.report.issues]
    assert r.spec.provenance.input_text == text      # 코드가 실제 입력으로 채움


def test_examples_cover_each_verdict_twice_and_do_not_leak_into_eval():
    assert [e[1]["verdict"] for e in EXAMPLES].count("ok") == 2
    assert [e[1]["verdict"] for e in EXAMPLES].count("clarify") == 2
    assert [e[1]["verdict"] for e in EXAMPLES].count("reject") == 2
    assert not {e[0] for e in EXAMPLES} & {s["text"] for s in EVAL}


def test_eval_set_shape():
    counts = {v: sum(s["verdict"] == v for s in EVAL) for v in ("ok", "clarify", "reject")}
    assert counts == {"ok": 10, "clarify": 12, "reject": 8}
    assert all(s["expected_spec"] == "TODO" for s in EVAL if s["verdict"] == "ok")
    assert len({s["text"] for s in EVAL}) == 30


def test_system_prompt_contains_dictionary_and_rules():
    p = build_system_prompt()
    assert all(k in p for k in INDICATORS)
    assert all(t in p for t in load_tickers())
    assert "이하" in p and "이상" in p and "FUTURE_REF" in p and "NO_EXIT_RULE" in p


def test_format_failure_retries_once_with_error_feedback(tmp_path):
    good = EXAMPLES[0][1]
    chat = fake_chat("{not json", good)
    r = specify(EXAMPLES[0][0], chat_fn=chat, cache_dir=tmp_path)
    assert r.attempts == 2 and r.next_step == "confirm"
    assert "형식 검증에 실패" in chat.calls[1][-1]["content"]


def test_format_failure_twice_gives_up(tmp_path):
    chat = fake_chat("{not json", "{still not json", EXAMPLES[0][1])
    r = specify("아무 문장", chat_fn=chat, cache_dir=tmp_path)
    assert r.attempts == 2 and r.next_step == "invalid" and r.report.failed_stage == 1


def test_non_format_failure_is_not_retried(tmp_path):
    bad = json.loads(json.dumps(EXAMPLES[0][1]))
    bad["exit"]["stop_loss"] = 7                        # 4단계(범위) 실패
    r = specify(EXAMPLES[0][0], chat_fn=fake_chat(bad, EXAMPLES[0][1]), cache_dir=tmp_path)
    assert r.attempts == 1 and r.next_step == "invalid" and r.report.failed_stage == 4


def test_cache_hit_skips_llm(tmp_path):
    text, out = EXAMPLES[1]
    first = specify(text, chat_fn=fake_chat(out), cache_dir=tmp_path)
    second = specify("  " + text + " ", chat_fn=fake_chat(), cache_dir=tmp_path)   # 호출되면 IndexError
    assert not first.cached and second.cached
    assert second.raw == first.raw and second.cache_key == first.cache_key
    assert second.next_step == "confirm"


def test_refine_combines_original_questions_answers(tmp_path):
    clar_text, clar_out = EXAMPLES[3]
    clar = specify(clar_text, chat_fn=fake_chat(clar_out), cache_dir=tmp_path)
    assert clar.next_step == "clarify"
    chat = fake_chat(EXAMPLES[0][1])
    refine(clar, ["삼성전자", "10% 오르면"], chat_fn=chat, cache_dir=tmp_path)
    sent = chat.calls[0][-1]["content"]
    assert sent.startswith(clar_text)
    for q in clar.report.questions:
        assert q in sent
    assert "삼성전자" in sent and "10% 오르면" in sent


def test_refine_rejects_answer_count_mismatch():
    with pytest.raises(ValueError):
        compose_refined_input("문장", ["q1", "q2"], ["a1"])


def _ok_out(**over):
    out = json.loads(json.dumps(EXAMPLES[0][1]))      # POSCO홀딩스 ok 예시
    out.update(over)
    return out


def test_llm_ok_with_unknown_ticker_goes_to_clarify(tmp_path):
    out = _ok_out(universe=[{"ticker": "UNKNOWN", "name": "UNKNOWN", "weight": 1.0}], unmapped_phrases=["에코프로"])
    out["provenance"]["mappings"][0]["phrase"] = "에코프로"
    r = specify("에코프로 10일선 뚫으면 사고 10일선 무너지면 팔아, 손절은 7%", chat_fn=fake_chat(out), cache_dir=tmp_path)
    assert r.llm_verdict == "ok" and r.verdict == "clarify" and r.next_step == "clarify"


def test_llm_ok_with_swapped_ticker_goes_to_clarify(tmp_path):
    """원문에 없는 종목(바꿔치기·기본값 삽입)은 백테스트로 가지 않는다."""
    out = _ok_out()
    out["provenance"]["mappings"] = out["provenance"]["mappings"][1:]
    r = specify("LG에너지솔루션 10일선 뚫으면 사고 10일선 무너지면 팔아, 손절은 7%", chat_fn=fake_chat(out),
                cache_dir=tmp_path)
    assert r.next_step == "clarify" and r.report.questions[0].startswith("어떤 종목")


def test_llm_ok_with_unmapped_fundamentals_goes_to_reject(tmp_path):
    out = _ok_out(unmapped_phrases=["PER 10 아래"])
    r = specify("POSCO홀딩스 10일선 뚫으면 사고 PER 10 아래여도 10일선 무너지면 팔아, 손절은 7%", chat_fn=fake_chat(out),
                cache_dir=tmp_path)
    assert r.next_step == "reject" and r.report.reject_reason.code == "UNSUPPORTED_CONCEPT"


def test_clarify_question_without_question_mark_retries_then_invalid(tmp_path):
    bad = {**EXAMPLES[3][1], "questions": ["RSI 30 아래면"]}
    chat = fake_chat(bad, bad)
    r = specify("RSI 30 아래면 사고 5일선 깨지면 팔아 (테스트)", chat_fn=chat, cache_dir=tmp_path)
    assert r.attempts == 2 and r.next_step == "invalid" and r.report.failed_stage == 1
    assert "물음표" in chat.calls[1][-1]["content"]


def test_refine_source_check_ignores_example_tickers_in_questions(tmp_path):
    """질문 예시에 '삼성전자'가 있어도, 사용자가 답하지 않은 종목은 원문 대조를 통과하지 못한다."""
    clar_text, clar_out = EXAMPLES[3]                    # "20일선 돌파하면 사" → 종목·매도 질문
    clar = specify(clar_text, chat_fn=fake_chat(clar_out), cache_dir=tmp_path)
    assert "삼성전자" in clar.report.questions[0]
    out = _ok_out(universe=[{"ticker": "005930", "name": "삼성전자", "weight": 1.0}],
                  entry={"logic": "all", "conditions": [{"left": "CLOSE", "op": "cross_above", "right": "SMA_20"}]})
    out["exit"] = {"logic": "any", "conditions": [{"left": "CLOSE", "op": "cross_below", "right": "SMA_5"}],
                   "stop_loss": None, "take_profit": None, "max_hold_days": None}
    out["provenance"]["mappings"] = [{"phrase": "20일선 돌파하면", "field": "entry.conditions[0]"}]
    r = refine(clar, ["KODEX 200", "5일선 깨지면"], chat_fn=fake_chat(out), cache_dir=tmp_path)
    assert r.next_step == "clarify"                       # 삼성전자는 질문 예시에만 있었음
    out["universe"] = [{"ticker": "069500", "name": "KODEX 200", "weight": 1.0}]
    r2 = refine(clar, ["KODEX 200", "5일선 깨지면"], chat_fn=fake_chat(out), cache_dir=tmp_path / "b")
    assert r2.next_step == "confirm"                      # 답변에 나온 종목은 통과
