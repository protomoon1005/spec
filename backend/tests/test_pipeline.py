"""컴파일 파이프라인과 추가 질문.

postgres · Redis · 시드가 필요하다. LLM 은 가짜를 넣는다 — 여기서 보려는 것은
**언제 멈춰서 묻고, 답을 받으면 어떻게 이어가는가** 다.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.m1 import pipeline, questions, session
from app.m1.postprocess import CompileError
from app.repositories import bbl


@pytest.fixture
def rebalance(engine):
    return bbl.get_block("RB_MONTHLY_FIRST").params_schema


@pytest.fixture
def make_owner(engine, make_user):
    """사용자를 만든다(user_id). 남긴 전략서까지 뒷정리한다 —
    안 지우면 make_user 가 사용자를 못 지워 다음 테스트가 깨진다."""
    created: list[int] = []

    def _make() -> int:
        _, user_id = make_user()
        created.append(user_id)
        return user_id

    yield _make

    with engine.begin() as conn:
        for user_id in created:
            conn.execute(
                text(
                    "DELETE FROM strategy_assets WHERE strategy_id IN"
                    " (SELECT strategy_id FROM strategies WHERE user_id = :u)"
                ),
                {"u": user_id},
            )
            conn.execute(text("DELETE FROM strategies WHERE user_id = :u"), {"u": user_id})


@pytest.fixture
def owner(make_owner):
    return make_owner()


class _Fake:
    """의도 추출과 전략서 생성 두 번 불린다. 의도는 호출마다 바꿔 끼울 수 있다."""

    def __init__(self, rebalance: dict, intents: list[dict]):
        self.rebalance = rebalance
        self.intents = list(intents)
        self.intent_calls = 0

    def generate_json(self, prompt: str, schema: dict, *, system: str | None = None) -> dict:
        if "keywords" in schema["properties"]:
            index = min(self.intent_calls, len(self.intents) - 1)
            self.intent_calls += 1
            return self.intents[index]

        option = max(
            schema["properties"]["universe"]["items"]["oneOf"],
            key=lambda o: len(o["properties"]["ticker"]["enum"]),
        )
        return {
            "strategy_id": "무시됨",
            "strategy_version": "0.1",
            "user_id": 0,
            "name": "테스트 전략",
            "created_at": "2026-09-20T09:00:00+09:00",
            "universe": [
                {
                    "ticker": option["properties"]["ticker"]["enum"][0],
                    "name": "덮어쓰여질 이름",
                    "weight_min": option["properties"]["weight_min"]["minimum"],
                    "weight_max": option["properties"]["weight_max"]["maximum"],
                }
            ],
            "rebalance": self.rebalance,
            "signal_rules": {"market_analysis": {"indicators": ["ret_20"]}},
            "constraint": {
                "max_weight_per_asset": 0.30,
                "min_weight_per_asset": 0.00,
                "cash_min": 0.05,
                "max_loss_per_trade": 0.05,
                "max_drawdown": 0.25,
            },
        }


def _intent(**overrides) -> dict:
    payload = {"keywords": [], "sectors": [], "asset_groups": [], "mentioned_names": []}
    payload.update(overrides)
    return payload


# --- 되묻지 않아도 되는 경우 -------------------------------------------------


def test_clear_request_goes_straight_through(engine, owner, rebalance):
    fake = _Fake(rebalance, [_intent(asset_groups=["BOND"])])

    result = pipeline.start(owner, "채권으로", fake)

    assert result.status == pipeline.STATUS_COMPLETED
    assert result.saved.strategy_id


def test_session_is_dropped_when_it_completes(engine, owner, rebalance):
    """끝난 대화를 남겨 둘 이유가 없다."""
    fake = _Fake(rebalance, [_intent(asset_groups=["BOND"])])

    result = pipeline.start(owner, "채권으로", fake)

    assert session.load(result.session_id) is None


# --- 되묻는 경우 -------------------------------------------------------------


def test_asks_when_there_is_no_direction(engine, owner, rebalance):
    """무엇을 원하는지 하나도 못 잡으면 물어야 한다. 아무거나 만들면 안 된다."""
    fake = _Fake(rebalance, [_intent()])

    result = pipeline.start(owner, "음", fake)

    assert result.status == pipeline.STATUS_NEED_ANSWER
    assert result.question.kind == questions.KIND_NO_DIRECTION
    assert result.question.choices


def test_asks_which_one_when_a_name_is_ambiguous(engine, owner, rebalance):
    """'국고채' 로는 여러 개가 걸린다. 엉뚱한 종목을 담는 것보다 되묻는 편이 낫다."""
    fake = _Fake(rebalance, [_intent(mentioned_names=["국고채"])])

    result = pipeline.start(owner, "국고채 넣어줘", fake)

    assert result.question.kind == questions.KIND_AMBIGUOUS_NAME
    assert len(result.question.choices) > 1
    assert all("국고채" in choice for choice in result.question.choices)


def test_question_carries_a_session_to_come_back_to(engine, owner, rebalance):
    fake = _Fake(rebalance, [_intent()])

    result = pipeline.start(owner, "음", fake)

    stored = session.load(result.session_id)
    assert stored is not None
    assert stored.user_text == "음"


# --- 답을 받아 이어가기 ------------------------------------------------------


def test_answer_continues_and_completes(engine, owner, rebalance):
    """답은 처음 요청 뒤에 이어 붙이고 처음부터 다시 돈다."""
    fake = _Fake(rebalance, [_intent(), _intent(asset_groups=["BOND"])])
    asked = pipeline.start(owner, "음", fake)

    result = pipeline.answer(asked.session_id, "채권으로 안전하게", fake)

    assert result.status == pipeline.STATUS_COMPLETED
    assert fake.intent_calls == 2


def test_answer_is_appended_to_the_original_request(engine, owner, rebalance):
    fake = _Fake(rebalance, [_intent(), _intent(asset_groups=["BOND"])])
    asked = pipeline.start(owner, "음", fake)

    stored = session.load(asked.session_id)
    stored.answers.append("채권으로")
    assert stored.full_text == "음\n채권으로"


def test_the_same_question_is_not_asked_twice(engine, owner, rebalance):
    """답을 받고도 같은 것이 모호하면 무한히 되묻게 된다. 한 번만 묻는다."""
    fake = _Fake(rebalance, [_intent(), _intent()])
    asked = pipeline.start(owner, "음", fake)

    result = pipeline.answer(asked.session_id, "잘 모르겠어", fake)

    assert result.status == pipeline.STATUS_COMPLETED


def test_expired_session_is_reported(engine, owner, rebalance):
    with pytest.raises(CompileError, match="만료"):
        pipeline.answer("없는세션", "아무말", _Fake(rebalance, [_intent()]))


# --- 담을 게 없을 때 ---------------------------------------------------------


def test_asks_again_when_nothing_can_be_held(engine, make_owner, rebalance, blocked_etfs):
    """거절 사유가 곧 답이다. 왜 못 담는지 알려 주지 않으면 같은 요청을 반복한다."""
    owner = make_owner()
    # 바이오 업종에는 보충할 수 있는 종목이 없다(레버리지뿐이다) — 지목한 상장폐지 종목만 남는다
    payload = _intent(mentioned_names=[blocked_etfs["delisted_name"]], sectors=["SECTOR_BIOHEALTH"])

    result = pipeline.start(owner, "이거로 크게", _Fake(rebalance, [payload]))

    assert result.status == pipeline.STATUS_NEED_ANSWER
    assert result.question.kind == questions.KIND_NOTHING_HOLDABLE
    assert any("상장폐지" in choice for choice in result.question.choices)


def test_gives_up_after_asking_once(engine, make_owner, rebalance, blocked_etfs):
    """두 번째에도 담을 게 없으면 포기하고 사유를 올린다. 무한히 되묻는 것보다 낫다."""
    owner = make_owner()
    payload = _intent(mentioned_names=[blocked_etfs["delisted_name"]], sectors=["SECTOR_BIOHEALTH"])
    fake = _Fake(rebalance, [payload, payload])
    asked = pipeline.start(owner, "이거로", fake)

    with pytest.raises(CompileError, match="담을 수 있는 종목이 없"):
        pipeline.answer(asked.session_id, "그래도 이거", fake)


# --- 손실 한도 · 제외 사유 (2026-10-27) ------------------------------------


class _Recording(_Fake):
    """전략서 생성에 들어간 프롬프트와 후보(스키마)를 남긴다."""

    def generate_json(self, prompt: str, schema: dict, *, system: str | None = None) -> dict:
        if "keywords" not in schema["properties"]:
            self.compile_prompt = prompt
        return super().generate_json(prompt, schema, system=system)


def test_spoken_loss_limit_overrides_the_model(engine, make_owner, rebalance):
    """모델은 max_drawdown 0.25 를 낸다. 문장의 '최대 5%' 가 이긴다."""
    owner = make_owner()
    fake = _Fake(rebalance, [_intent(asset_groups=["BOND"])])

    result = pipeline.start(owner, "채권으로, 최대 5%만 잃고 싶어", fake)

    assert result.loss_limit is not None and result.loss_limit.applied == pytest.approx(0.05)
    with engine.connect() as conn:
        stored = conn.execute(
            text("SELECT constraint_user FROM strategies WHERE strategy_id = :s"),
            {"s": result.saved.strategy_id},
        ).scalar_one()
    assert stored["max_drawdown"] == pytest.approx(0.05)


def test_loose_loss_limit_is_kept_as_spoken(engine, make_owner, rebalance):
    """성향 한도가 없으므로 사용자가 말한 값을 그대로 쓴다."""
    owner = make_owner()
    fake = _Fake(rebalance, [_intent(asset_groups=["BOND"])])

    result = pipeline.start(owner, "채권으로, 손실은 40% 까지 괜찮아", fake)

    assert result.loss_limit.applied == pytest.approx(0.40)


def test_completed_result_lists_what_was_excluded(engine, make_owner, rebalance, blocked_etfs):
    """지목한 상장폐지 종목을 빼고 만들었으면 그 사실과 사유가 작업 결과에 남는다."""
    owner = make_owner()
    payload = _intent(mentioned_names=[blocked_etfs["delisted"]], asset_groups=["BOND"])

    result = pipeline.start(owner, "이거 담아줘", _Fake(rebalance, [payload]))

    assert result.status == pipeline.STATUS_COMPLETED
    assert {
        "ticker": blocked_etfs["delisted"],
        "name": blocked_etfs["delisted_name"],
        "reason_code": "DELISTED",
        "reason": "상장폐지된 종목이다",
    } in result.excluded


def test_picking_a_delisted_choice_is_not_silently_replaced(engine, make_owner, rebalance, blocked_etfs):
    """되묻기에서 상장폐지 종목을 고르면, 의도 추출이 이름을 놓쳐도 종목코드로 지목된다."""
    owner = make_owner()
    fake = _Fake(rebalance, [_intent(mentioned_names=["테스트"]), _intent(asset_groups=["BOND"])])
    asked = pipeline.start(owner, "테스트 담아줘", fake)
    choice = next(c for c in asked.question.choices if c.startswith(blocked_etfs["delisted"]))
    assert choice.endswith(questions.DELISTED_LABEL)

    result = pipeline.answer(asked.session_id, choice, fake)

    assert result.status == pipeline.STATUS_COMPLETED
    codes = [e["reason_code"] for e in result.excluded if e["ticker"] == blocked_etfs["delisted"]]
    assert codes == ["DELISTED"]


def test_compile_task_result_carries_excluded_and_loss_limit(engine, make_owner, rebalance, monkeypatch):
    from app.workers import tasks

    owner = make_owner()
    fake = _Fake(rebalance, [_intent(keywords=["수익"])])
    monkeypatch.setattr("app.llm.client.get_llm_client", lambda: fake)

    out = tasks.compile_spec.run(
        {"user_id": owner, "input_prompt": "수익률 높게, 손실은 5% 이내"}
    )

    assert out["status"] == "completed"
    assert out["loss_limit"]["applied"] == pytest.approx(0.05)
    assert out["loss_limit"]["matched"]
    assert out["excluded"] == []
