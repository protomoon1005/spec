"""전략서 v0.2 명세화 입구(app/m1/compile_v0_2.py) 테스트.

테스트 DB 의 종목 원장을 쓰고(conftest 가 시드를 넣는다), LLM 은 가짜, 대화 상태는 메모리에 둔다.
"""

from __future__ import annotations

import copy

import pytest
from sqlalchemy import text

from app.m1 import compile_v0_2, pairing
from app.repositories import specs

REPRESENTATIVE = "삼성전자 RSI 30 아래로 떨어지면 사고 5일선 깨지면 팔아줘"


class FakeClient:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = 0

    def generate_json(self, prompt, json_schema, *, system=None):
        self.calls += 1
        return self.answers.pop(0)


@pytest.fixture
def user(make_user):
    _, user_id = make_user()
    created: list[str] = []
    yield user_id, created
    for strategy_id in created:
        assert specs.delete_draft_spec(strategy_id, user_id) == "deleted"


def test_representative_sentence_is_saved_as_a_draft(user, engine):
    user_id, created = user
    store = compile_v0_2.MemoryStore()
    client = FakeClient(copy.deepcopy(pairing.EXAMPLE_OUTPUT))
    result = compile_v0_2.start(user_id, REPRESENTATIVE, client, store=store)
    assert result.status == "ok", result
    created.append(result.strategy_id)
    assert client.calls == 1 and store.data == {}

    saved = specs.get_strategy_v0_2(result.strategy_id)
    assert saved["user_id"] == user_id and saved["status"] == "draft"
    assert saved["name"] == "삼성전자 · 매매 조건형"
    assert saved["spec"]["rules"][0]["entry"]["op"] == "cross_below"
    with engine.connect() as conn:
        assets = conn.execute(
            text("SELECT ticker, weight_min FROM strategy_assets WHERE strategy_id = :s"),
            {"s": result.strategy_id},
        ).all()
        logs = conn.execute(
            text("SELECT stage, passed, violations FROM validation_logs WHERE strategy_id = :s"),
            {"s": result.strategy_id},
        ).all()
    assert [(a.ticker, float(a.weight_min)) for a in assets] == [("005930", 1.0)]
    assert [(log.stage, log.passed, log.violations) for log in logs] == [(4, True, [])]
    public = result.public()
    assert public["status"] == "ok" and public["spec"]["universe"]["tickers"] == ["005930"]


def test_ask_then_answer_without_calling_ai_again(user):
    user_id, created = user
    text_ = "삼성전자 RSI 30 아래로 내려가면 매수"
    raw = copy.deepcopy(pairing.EXAMPLE_OUTPUT)
    raw["rules"][0]["entry"] = {
        "actions": ["5:매수"],
        "join": "one",
        "items": [{"subject": None, "left": "2:RSI", "op": "4:아래로 내려가면", "right": "3:30"}],
    }
    raw["rules"][0]["exit"] = None
    store = compile_v0_2.MemoryStore()
    client = FakeClient(raw)
    first = compile_v0_2.start(user_id, text_, client, store=store)
    assert first.status == "ask" and first.round == 1
    assert [q["text"] for q in first.questions] == ["언제 팔까요?"]
    assert "value" not in str(first.public())

    done = compile_v0_2.answer(first.session_id, [{"question_id": "q1", "choice_id": "c2"}], store=store)
    assert done.status == "ok", done
    created.append(done.strategy_id)
    assert client.calls == 1
    exit_ = done.spec["rules"][0]["exit"]
    assert (exit_["left"]["indicator"], exit_["op"], exit_["right"]["value"]) == ("entry_return", ">=", 0.1)


def test_free_text_answer(user):
    user_id, created = user
    raw = copy.deepcopy(pairing.EXAMPLE_OUTPUT)
    raw["rules"][0]["exit"]["items"][0]["right"] = "6:이평선"
    raw["rules"][0]["exit"]["actions"] = ["8:팔아"]
    store = compile_v0_2.MemoryStore()
    first = compile_v0_2.start(
        user_id, "삼성전자 RSI 30 아래로 떨어지면 사고 이평선 깨지면 팔아", FakeClient(raw), store=store
    )
    assert [q["kind"] for q in first.questions] == ["ma_window"]
    done = compile_v0_2.answer(first.session_id, [{"question_id": "q1", "text": "20일선으로"}], store=store)
    assert done.status == "ok", done
    created.append(done.strategy_id)
    assert done.spec["rules"][0]["exit"]["right"]["window"] == 20


def test_reject_before_ai_and_expired_session():
    client = FakeClient()
    result = compile_v0_2.start(
        1, "삼성전자 MACD 골든크로스 나면 사", client, store=compile_v0_2.MemoryStore()
    )
    assert result.status == "reject" and result.rejection["code"] == "UNKNOWN_INDICATOR"
    assert client.calls == 0
    assert compile_v0_2.start(1, "엑셀에서 VLOOKUP 쓰는 법", client).public()["code"] == "OUT_OF_DOMAIN"
    with pytest.raises(LookupError):
        compile_v0_2.answer("없는세션", [], store=compile_v0_2.MemoryStore())
