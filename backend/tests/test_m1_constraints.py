"""손실 문구의 확정적 추출 · 제외 사유 · LLM 재현성 옵션 (2026-10-27 사용자 결정).

정규식과 LLM 요청 본문은 DB 없이 돈다. 후보·제외 사유 테스트는 postgres 와 시드가 필요하다.
"""
from __future__ import annotations

import pytest

from app.m1 import candidates as C
from app.m1 import constraints, excluded, questions
from app.m1 import intent as I

# --- 손실 한도 정규식 (DB 없음) -------------------------------------------------


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("최대 5%만 잃고 싶은데 기술주로 대박 내줘", 0.05),
        ("10% 넘게 잃으면 안 돼", 0.10),
        ("손실은 15% 이내로 해줘", 0.15),
        ("손실 7.5% 이하", 0.075),
        ("낙폭은 20프로까지만", 0.20),
        ("8퍼센트 이상 떨어지면 곤란해", 0.08),
        ("MDD 12% 안쪽으로", 0.12),
        ("-10% 넘게 빠지면 안 된다", 0.10),
        ("수익은 20% 정도 내고 손실은 5% 이내", 0.05),  # 절이 갈린다
        ("20% 수익에 손실은 5%", 0.05),  # 한 절에 숫자가 둘이면 손실 낱말에 가까운 것
    ],
)
def test_explicit_loss_limits_are_read(text, value):
    found = constraints.find_loss_limit(text)

    assert found is not None
    assert found[0] == pytest.approx(value)


@pytest.mark.parametrize(
    "text",
    [
        "요즘 반도체가 흥한다던데 거기 비중을 좀 싣되 크게 잃지는 않게 해줘",  # 숫자 없음
        "수익률 높게 가고 손실은 어느 정도 감수할게",
        "한 종목에 30% 넘게는 싫어",  # 손실 낱말 없음
        "KODEX 200 하나만 담아줘. 최대 30%까지만",
        "수익률 10% 이상",
        "손실은 150% 까지",  # 비율 밖
        "반도체 비중을 10%만 싣되 크게 잃지는 않게 해줘",  # 비중 % 는 손실 한도가 아니다
        "",
    ],
)
def test_no_limit_without_a_number_next_to_a_loss_word(text):
    assert constraints.find_loss_limit(text) is None


def test_spoken_limit_is_used_as_is():
    """성향 한도가 없으므로 말한 값을 그대로 쓴다(주제 변경 결정 3)."""
    limit = constraints.loss_limit_for("손실은 30% 까지 괜찮아")

    assert limit.requested == pytest.approx(0.30)
    assert limit.applied == pytest.approx(0.30)
    assert limit.field == "constraint.max_drawdown"
    assert "30%" in limit.matched


def test_limit_overrides_the_model_output_without_touching_it():
    raw = {"constraint": {"max_drawdown": 0.20, "cash_min": 0.1}, "name": "x"}
    limit = constraints.loss_limit_for("최대 5%만 잃고")

    out = constraints.apply_loss_limit(raw, limit)

    assert out["constraint"] == {"max_drawdown": 0.05, "cash_min": 0.1}
    assert raw["constraint"]["max_drawdown"] == 0.20
    assert constraints.apply_loss_limit(raw, None) is raw


# --- LLM 재현성 옵션 (DB 없음) --------------------------------------------------


def test_ollama_payload_fixes_temperature_and_seed():
    from app.core.config import get_settings
    from app.llm.ollama_client import OllamaClient

    settings = get_settings()
    payload = OllamaClient(base_url="http://x", model="m").build_payload("p", {"type": "object"}, system="s")

    assert payload["options"] == {"temperature": settings.llm_temperature, "seed": settings.llm_seed}
    assert settings.llm_temperature == 0.0
    assert payload["format"] == {"type": "object"}
    assert payload["think"] is False
    assert [m["role"] for m in payload["messages"]] == ["system", "user"]


# --- 제외 사유 (excluded) -------------------------------------------------------

KEYS = {"ticker", "name", "reason_code", "reason"}


def test_excluded_tickers_carry_code_and_reason(engine, blocked_etfs):
    requested = [blocked_etfs["delisted"], blocked_etfs["leveraged"], "999999"]
    result = C.select(requested_tickers=requested)

    out = excluded.build(result)

    assert all(set(item) == KEYS for item in out)
    by_ticker = {item["ticker"]: item for item in out}
    assert by_ticker[blocked_etfs["delisted"]]["reason_code"] == "DELISTED"
    assert by_ticker[blocked_etfs["delisted"]]["reason"] == C.REASON_DELISTED
    assert by_ticker[blocked_etfs["delisted"]]["name"] == blocked_etfs["delisted_name"]
    assert blocked_etfs["leveraged"] not in by_ticker  # 레버리지는 막지 않는다
    assert by_ticker["999999"]["reason_code"] == "NOT_FOUND"


# --- 추가 질문의 상장폐지 표시와 답의 종목코드 ------------------------------------


def test_delisted_choice_is_labelled(engine, blocked_etfs):
    resolved = I.ResolvedIntent(intent=I.Intent(), tag_queries=(), requested_tickers=(),
                                unresolved_names=("테스트 상장폐지",))

    question = questions.for_intent(resolved)

    delisted = [c for c in question.choices if c.startswith(blocked_etfs["delisted"])]
    assert delisted and delisted[0].endswith(questions.DELISTED_LABEL)
    assert all(not c.endswith(questions.DELISTED_LABEL) for c in question.choices if c not in delisted)


def test_answer_with_a_ticker_becomes_a_requested_ticker(engine, blocked_etfs):
    answer = f"{blocked_etfs['delisted']} {blocked_etfs['delisted_name']}{questions.DELISTED_LABEL}"

    assert I.tickers_in_answers([answer, "안전하게", ""]) == (blocked_etfs["delisted"],)
    resolved = I.with_answered_tickers(I.resolve(I.Intent()), [answer])
    assert resolved.requested_tickers == (blocked_etfs["delisted"],)
