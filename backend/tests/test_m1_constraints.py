"""손실 문구의 확정적 추출 · 채권 보충 · 제외 사유 · LLM 재현성 옵션 (2026-10-27 사용자 결정).

정규식과 LLM 요청 본문은 DB 없이 돈다. 후보·제외 사유 테스트는 postgres 와 시드가 필요하다.
"""
from __future__ import annotations

import pytest

from app.m1 import candidates as C
from app.m1 import constraints, excluded, questions
from app.m1 import intent as I
from app.repositories import etf_master, presets

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


def test_looser_than_the_profile_limit_is_cut_to_it():
    limit = constraints.loss_limit_for("손실은 30% 까지 괜찮아", profile_limit=0.20)

    assert limit.requested == pytest.approx(0.30)
    assert limit.applied == pytest.approx(0.20)
    assert limit.clamped_to_profile is True
    assert limit.field == "constraint.max_drawdown"
    assert "30%" in limit.matched


def test_tighter_than_the_profile_limit_is_kept():
    limit = constraints.loss_limit_for("최대 5%만 잃고 싶어", profile_limit=0.20)

    assert limit.applied == pytest.approx(0.05)
    assert limit.clamped_to_profile is False


def test_limit_overrides_the_model_output_without_touching_it():
    raw = {"constraint": {"max_drawdown": 0.20, "cash_min": 0.1}, "name": "x"}
    limit = constraints.loss_limit_for("최대 5%만 잃고", profile_limit=0.20)

    out = constraints.apply_loss_limit(raw, limit)

    assert out["constraint"] == {"max_drawdown": 0.05, "cash_min": 0.1}
    assert raw["constraint"]["max_drawdown"] == 0.20
    assert constraints.apply_loss_limit(raw, None) is raw


@pytest.mark.parametrize(
    "text",
    ["크게 잃지는 않게 해줘", "안 잃게", "손실은 최소로", "원금은 지키고 싶어", "손해는 덜 보게"],
)
def test_loss_averse_phrases_without_numbers(text, monkeypatch):
    monkeypatch.setattr(constraints, "loss_averse_keywords", lambda: frozenset())

    assert constraints.is_loss_averse(text, (), has_limit=False)


def test_accepting_losses_is_not_loss_averse(monkeypatch):
    monkeypatch.setattr(constraints, "loss_averse_keywords", lambda: frozenset())

    text = "수익률 높게 가고 손실은 어느 정도 감수할게"
    assert not constraints.is_loss_averse(text, ("수익",), has_limit=False)


def test_explicit_limit_counts_as_loss_averse():
    assert constraints.is_loss_averse("아무 말", (), has_limit=True)


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


def test_vllm_payload_fixes_temperature_and_seed():
    from app.core.config import get_settings
    from app.llm.vllm_client import VLLMClient

    settings = get_settings()
    payload = VLLMClient(base_url="http://x", model="m").build_payload("p", {"type": "object"})

    assert payload["temperature"] == settings.llm_temperature
    assert payload["seed"] == settings.llm_seed
    assert payload["guided_json"] == {"type": "object"}


# --- 손실 회피 낱말은 BBL 에서 온다 ---------------------------------------------


def test_loss_averse_keywords_come_from_the_low_risk_filter_block(engine):
    words = constraints.loss_averse_keywords()

    assert words and "안전하게" in words
    assert constraints.is_loss_averse("아무 말", ("안전하게",), has_limit=False)


# --- 채권 보충 ------------------------------------------------------------------


def _holdable_bonds(level: int) -> list[str]:
    bounds = presets.get_asset_bounds(level)
    return [
        r.ticker
        for r in etf_master.search(group_ids=["BOND"])
        if r.risk_tag in bounds and bounds[r.risk_tag].allowed_max > 0
    ]


def test_guard_adds_holdable_bonds_after_the_sector(engine):
    plain = C.select(risk_level=3, sectors=["SECTOR_SEMICONDUCTOR"], group_ids=["EQUITY"])
    guarded = C.select(
        risk_level=3, sectors=["SECTOR_SEMICONDUCTOR"], group_ids=["EQUITY"], guard_bonds=True
    )

    guards = [c for c in guarded.candidates if c.guard]
    sector = [c for c in guarded.candidates if not c.guard]
    assert guards, "채권이 보충되지 않았다"
    assert all(c.asset_group_id == "BOND" and c.weight_max > 0 for c in guards)
    assert all(c.sector_group_id == "SECTOR_SEMICONDUCTOR" for c in sector)
    # 섹터가 먼저, 채권이 뒤 — 순서 규칙(종목코드 순)은 기존 보충과 같다
    assert list(guarded.candidates[: len(sector)]) == sector
    assert [c.ticker for c in guards] == _holdable_bonds(3)[: len(guards)]
    assert len(guarded.candidates) <= C.DEFAULT_TARGET
    assert len(guards) >= min(C.BOND_RESERVE, len(_holdable_bonds(3)))
    assert not any(c.guard for c in plain.candidates)


def test_guard_reserves_room_even_when_the_sector_fills_the_target(engine):
    guarded = C.select(risk_level=5, sectors=["SECTOR_SEMICONDUCTOR"], target=3, guard_bonds=True)

    assert sum(c.guard for c in guarded.candidates) == C.BOND_RESERVE
    assert len(guarded.candidates) == 3


def test_guard_never_replaces_an_empty_sector_with_bonds(engine):
    # 성향 1 은 반도체(G2)를 못 담는다. 채권으로 조용히 갈아끼우지 않고 빈 후보 → 되묻기로 간다
    guarded = C.select(risk_level=1, sectors=["SECTOR_SEMICONDUCTOR"], group_ids=["EQUITY"], guard_bonds=True)

    assert guarded.candidates == ()


def test_guard_follows_the_sector_of_a_named_ticker(engine):
    # 섹터 낱말 없이 종목 이름(KODEX 반도체)만 말해도 그 업종으로 보충하고 채권 자리도 남긴다
    guarded = C.select(risk_level=3, requested_tickers=["091160"], guard_bonds=True)

    assert any(c.guard for c in guarded.candidates), "채권이 보충되지 않았다"


def test_guard_needs_a_named_sector(engine):
    # 섹터도 지목 종목도 없이 자산군만 말하면 채권을 덧붙이지 않는다(기존 동작)
    guarded = C.select(risk_level=3, group_ids=["EQUITY"], guard_bonds=True)

    assert not any(c.guard for c in guarded.candidates)


def test_guard_is_deterministic(engine):
    kwargs = {"risk_level": 3, "sectors": ["SECTOR_SEMICONDUCTOR"], "guard_bonds": True}

    assert C.select(**kwargs).tickers == C.select(**kwargs).tickers


def test_prompt_marks_guard_bonds(engine):
    from app.m1 import prompt

    text = prompt.build_compile_prompt(
        user_text="반도체에 싣되 크게 잃지 않게",
        candidate_set=C.select(risk_level=3, sectors=["SECTOR_SEMICONDUCTOR"], guard_bonds=True),
        defaults={"cash_min_default": 0.1, "max_drawdown_default": 0.2, "max_loss_per_trade_default": 0.05},
        loss_limit=0.05,
    )

    assert "(손실 제약을 지키려고 보충한 채권)" in text
    assert "최대 낙폭 0.05 (사용자가 말한 손실 한도" in text


# --- 제외 사유 (excluded) -------------------------------------------------------

KEYS = {"ticker", "name", "reason_code", "reason"}


def test_excluded_tickers_carry_code_and_reason(engine, blocked_etfs):
    requested = [blocked_etfs["delisted"], blocked_etfs["leveraged"], "999999"]
    result = C.select(risk_level=3, requested_tickers=requested)

    out = excluded.build(result)

    assert all(set(item) == KEYS for item in out)
    by_ticker = {item["ticker"]: item for item in out}
    assert by_ticker[blocked_etfs["delisted"]]["reason_code"] == "DELISTED"
    assert by_ticker[blocked_etfs["delisted"]]["reason"] == C.REASON_DELISTED
    assert by_ticker[blocked_etfs["delisted"]]["name"] == blocked_etfs["delisted_name"]
    assert by_ticker[blocked_etfs["leveraged"]]["reason_code"] == "LEVERAGE_FORBIDDEN"
    assert by_ticker["999999"]["reason_code"] == "NOT_FOUND"


def test_preset_zero_reason_names_the_profile(engine):
    blocked = next(
        r for r in etf_master.search(limit=300) if presets.get_asset_bounds(1)[r.risk_tag].allowed_max == 0
    )

    out = excluded.build(C.select(risk_level=1, requested_tickers=[blocked.ticker]))

    assert out[0]["reason_code"] == "PRESET_ZERO"
    assert out[0]["reason"] == f"성향 1(안정투자형) 의 {blocked.risk_tag} 허용 상한이 0 이다"


def test_a_whole_risk_tier_out_of_reach_is_reported_as_a_group(engine):
    # 케이스 S1: 안정투자형이 "수익률 높게" — kw:수익 → FL_HIGH_RISK_ONLY(G1·G2), 성향 1 은 둘 다 0
    resolved = I.resolve(I.Intent(keywords=("수익",)))
    out = excluded.build(C.select(risk_level=1), resolved)

    groups = [item for item in out if item["ticker"] is None]
    assert groups and all(set(item) == KEYS for item in groups)
    assert groups[0]["reason_code"] == excluded.CODE_GROUP_PRESET_ZERO
    assert "G1·G2" in groups[0]["name"]
    assert groups[0]["reason"].startswith("성향 1(안정투자형) 의 ")


def test_a_named_sector_out_of_reach_is_reported(engine):
    resolved = I.resolve(I.Intent(sectors=("SECTOR_SEMICONDUCTOR",)))
    out = excluded.build(C.select(risk_level=1, sectors=["SECTOR_SEMICONDUCTOR"]), resolved)

    assert [item["name"] for item in out if item["ticker"] is None] == ["반도체 업종"]


def test_partly_holdable_group_is_not_reported(engine):
    # 성향 1 도 주식 G4(고배당 등)는 담는다 — 통째로 빠진 게 아니면 남기지 않는다
    resolved = I.resolve(I.Intent(asset_groups=("EQUITY",)))

    assert excluded.build(C.select(risk_level=1, group_ids=["EQUITY"]), resolved) == []
    assert excluded.build(C.select(risk_level=5), I.resolve(I.Intent(keywords=("수익",)))) == []


# --- 되묻기의 상장폐지 표시와 답의 종목코드 ------------------------------------


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
