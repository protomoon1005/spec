"""전략서 생성과 후처리 (M1 5단계).

postgres 와 시드가 적재된 상태여야 한다. LLM 은 가짜를 넣는다 — 여기서 보려는 것은
**모델이 무엇을 내든 쓸 수 없는 전략서는 통과하지 못한다**는 것이다.
실제 모델 호출은 맨 뒤 하나뿐이고 `requires_ollama` 가 붙어 있다.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.m1 import candidates as C
from app.m1 import pipeline, postprocess
from app.m1.postprocess import CompileError
from app.repositories import bbl, profiles, specs

USER_TEXT = "안전하게 굴려줘"


@pytest.fixture
def rebalance(engine):
    return bbl.get_block("RB_MONTHLY_FIRST").params_schema


@pytest.fixture
def candidate_set(engine):
    return C.select(risk_level=5, target=3)


def _payload(candidate_set, rebalance, **overrides) -> dict:
    universe = [
        {
            "ticker": c.ticker,
            "name": c.name,
            "weight_min": c.weight_min,
            "weight_max": c.weight_max,
        }
        for c in candidate_set.candidates
    ]
    payload = {
        "spec_id": "무시됨",
        "spec_version": "0.1",
        "user_id": 999,
        "name": "테스트 전략",
        "created_at": "2026-09-20T09:00:00+09:00",
        "universe": universe,
        "rebalance": rebalance,
        "signal_rules": {"market_analysis": {"indicators": ["ret_20"]}},
        "constraint": {
            "max_weight_per_asset": 0.30,
            "min_weight_per_asset": 0.00,
            "cash_min": 0.05,
            "max_loss_per_trade": 0.05,
            "max_drawdown": 0.25,
        },
    }
    payload.update(overrides)
    return payload


def _parse(payload, candidate_set):
    return postprocess.parse(payload, candidate_set, spec_id="STR-TEST", user_id=7)


# --- 파싱 -------------------------------------------------------------------


def test_identity_is_not_taken_from_the_model(engine, candidate_set, rebalance):
    """사용자 번호를 지어내면 남의 전략서가 되고, 전략서 번호가 겹치면 저장이 깨진다."""
    compiled = _parse(_payload(candidate_set, rebalance), candidate_set)

    assert compiled.spec.spec_id == "STR-TEST"
    assert compiled.spec.user_id == 7


def test_ticker_outside_the_candidates_is_rejected(engine, candidate_set, rebalance):
    """Ollama 는 형식 강제가 약해 스키마를 어긴 출력이 실제로 나온다. 여기가 유일한 방어선이다."""
    payload = _payload(candidate_set, rebalance)
    payload["universe"][0]["ticker"] = "999999"

    with pytest.raises(CompileError, match="후보에 없는 종목"):
        _parse(payload, candidate_set)


def test_weight_above_the_profile_cap_is_rejected(engine, candidate_set, rebalance):
    payload = _payload(candidate_set, rebalance)
    payload["universe"][0]["weight_max"] = 0.99

    with pytest.raises(CompileError, match="상한을 넘었다"):
        _parse(payload, candidate_set)


def test_min_above_max_is_rejected(engine, candidate_set, rebalance):
    """JSON Schema 로는 칸끼리의 관계를 표현할 수 없다. 여기서 봐야 한다."""
    payload = _payload(candidate_set, rebalance)
    item = payload["universe"][0]
    item["weight_min"], item["weight_max"] = item["weight_max"], 0.0

    with pytest.raises(CompileError, match="하한이 상한보다"):
        _parse(payload, candidate_set)


def test_duplicate_ticker_is_rejected(engine, candidate_set, rebalance):
    payload = _payload(candidate_set, rebalance)
    payload["universe"].append(dict(payload["universe"][0]))

    with pytest.raises(CompileError, match="두 번"):
        _parse(payload, candidate_set)


def test_infeasible_floor_is_rejected(engine, candidate_set, rebalance):
    """하한의 합과 최소 현금이 1 을 넘으면 어떤 비중을 골라도 만족시킬 수 없다."""
    payload = _payload(candidate_set, rebalance)
    for item in payload["universe"]:
        item["weight_min"] = item["weight_max"]
    payload["constraint"]["cash_min"] = 0.95

    with pytest.raises(CompileError, match="1 을 넘는다"):
        _parse(payload, candidate_set)


def test_malformed_payload_is_reported_not_crashed(engine, candidate_set):
    with pytest.raises(CompileError, match="형식을 벗어났다"):
        _parse({"name": "쓰레기"}, candidate_set)


# --- 저장 -------------------------------------------------------------------


@pytest.fixture
def profile(engine, make_user):
    email, user_id = make_user("retail")
    version = profiles.active_preset_version()
    defaults = profiles.get_profile_defaults(version, 5)
    record = profiles.insert_profile(
        user_id=user_id, risk_level=5, preset_version=version, defaults=defaults, provenance={}
    )
    yield record
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM spec_universe WHERE spec_id IN"
                " (SELECT spec_id FROM strategy_specs WHERE user_id = :u)"
            ),
            {"u": user_id},
        )
        conn.execute(text("DELETE FROM strategy_specs WHERE user_id = :u"), {"u": user_id})
        conn.execute(text("DELETE FROM risk_profiles WHERE user_id = :u"), {"u": user_id})


def test_save_writes_spec_and_universe_together(engine, candidate_set, rebalance, profile):
    compiled = postprocess.parse(
        _payload(candidate_set, rebalance),
        candidate_set,
        spec_id=specs.new_spec_id(),
        user_id=profile.user_id,
    )

    saved = postprocess.save(
        compiled, user_id=profile.user_id, profile_id=profile.profile_id, input_prompt=USER_TEXT
    )

    assert saved.status == "draft"
    assert saved.universe_size == len(candidate_set.candidates)
    rows = specs.get_spec_universe(saved.spec_id)
    assert {row["ticker"] for row in rows} == set(candidate_set.tickers)


def test_saved_rows_keep_raw_and_final_weights(engine, candidate_set, rebalance, profile):
    """M1 단계에서는 스키마가 이미 범위를 강제했으므로 둘이 같고 조정 표시가 없다.
    하드캡으로 접는 일은 Validator 몫이고, 그때 이 둘이 갈라진다."""
    compiled = postprocess.parse(
        _payload(candidate_set, rebalance),
        candidate_set,
        spec_id=specs.new_spec_id(),
        user_id=profile.user_id,
    )
    saved = postprocess.save(compiled, user_id=profile.user_id, profile_id=profile.profile_id)

    for row in specs.get_spec_universe(saved.spec_id):
        assert row["weight_min"] == row["weight_min_raw"]
        assert row["weight_max"] == row["weight_max_raw"]
        assert row["was_adjusted"] is False
        assert row["preset_id"] > 0


def test_saved_spec_records_which_hardcap_applied(engine, candidate_set, rebalance, profile):
    compiled = postprocess.parse(
        _payload(candidate_set, rebalance),
        candidate_set,
        spec_id=specs.new_spec_id(),
        user_id=profile.user_id,
    )
    saved = postprocess.save(compiled, user_id=profile.user_id, profile_id=profile.profile_id)

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT hardcap_version, status, input_prompt FROM strategy_specs WHERE spec_id = :s"),
            {"s": saved.spec_id},
        ).one()
    assert row.hardcap_version == "v0.1"
    assert row.status == "draft"


# --- 전 구간 (가짜 모델) -----------------------------------------------------


class _FakeLLM:
    """의도 추출과 전략서 생성 두 번 불린다. 스키마를 보고 무엇을 낼지 고른다."""

    def __init__(self, rebalance: dict):
        self.rebalance = rebalance
        self.schemas: list[dict] = []
        self.picked: list[str] = []

    def generate_json(self, prompt: str, schema: dict, *, system: str | None = None) -> dict:
        self.schemas.append(schema)
        if "keywords" in schema["properties"]:
            return {
                "keywords": ["안전하게"],
                "sectors": [],
                "asset_groups": ["BOND"],
                "mentioned_names": [],
            }
        # 선택지는 허용 범위가 같은 종목끼리 묶여 있다. 가장 큰 묶음에서 최대 둘을 고른다.
        option = max(
            schema["properties"]["universe"]["items"]["oneOf"],
            key=lambda o: len(o["properties"]["ticker"]["enum"]),
        )
        tickers = option["properties"]["ticker"]["enum"][:2]
        self.picked = list(tickers)
        return {
            "spec_id": "무시됨",
            "spec_version": "0.1",
            "user_id": 0,
            "name": "보수적 채권 전략",
            "created_at": "2026-09-20T09:00:00+09:00",
            "universe": [
                {
                    "ticker": ticker,
                    # 모델이 이름을 틀리게 써도 후처리가 덮어쓴다.
                    "name": "모델이 지어낸 이름",
                    "weight_min": option["properties"]["weight_min"]["minimum"],
                    "weight_max": option["properties"]["weight_max"]["maximum"],
                }
                for ticker in tickers
            ],
            "rebalance": self.rebalance,
            "signal_rules": {"market_analysis": {"indicators": ["ret_20", "rsi_14"]}},
            "constraint": {
                "max_weight_per_asset": 0.30,
                "min_weight_per_asset": 0.00,
                "cash_min": 0.05,
                "max_loss_per_trade": 0.05,
                "max_drawdown": 0.25,
            },
        }


def test_compile_end_to_end_with_a_fake_model(engine, rebalance, profile):
    fake = _FakeLLM(rebalance)

    result = pipeline.start(profile.user_id, USER_TEXT, fake)

    assert result.status == pipeline.STATUS_COMPLETED
    assert result.saved.status == "draft"
    assert result.saved.universe_size == len(fake.picked)
    assert {row["ticker"] for row in specs.get_spec_universe(result.saved.spec_id)} == set(fake.picked)


def test_compile_needs_a_confirmed_profile(engine, make_user, rebalance):
    """성향을 모르면 종목별 허용 범위가 정해지지 않는다."""
    _, user_id = make_user("retail")

    with pytest.raises(CompileError, match="투자 성향"):
        pipeline.start(user_id, USER_TEXT, _FakeLLM(rebalance))


# --- 실제 모델 ---------------------------------------------------------------


@pytest.mark.requires_ollama
def test_compile_with_the_real_model(engine, profile):
    """호스트 Ollama 로 한 번 끝까지 돌린다.

    무엇을 고르는지는 모델이 정하는 것이라 고정하지 않는다. 보려는 것은
    **나온 전략서가 우리 규칙 안에 있는가** 다 — 후보 밖 종목이 없고, 비중이 그 성향의
    범위 안이고, 실행 가능한가. Ollama 는 형식 강제가 약해서 이게 실제로 깨질 수 있다.
    """
    from app.llm.client import get_llm_client

    result = pipeline.start(
        profile.user_id, "안전하게 채권 위주로 굴리고 매달 정리해줘", get_llm_client()
    )

    assert result.status == pipeline.STATUS_COMPLETED
    rows = specs.get_spec_universe(result.saved.spec_id)
    assert rows
    bounds = {c.ticker: c for c in C.select(risk_level=profile.risk_level, target=300).candidates}
    for row in rows:
        assert row["ticker"] in bounds
        assert row["weight_min"] <= row["weight_max"]
        assert float(row["weight_max"]) <= bounds[row["ticker"]].weight_max


def test_model_supplied_name_is_overwritten(engine, candidate_set, rebalance):
    """종목명은 종목코드만 정해지면 우리가 아는 값이다. 스키마에서 고정하지 않은 대신
    여기서 덮어쓴다 — 모델에게 정확한 문자열을 받아 내려고 문법을 키울 이유가 없다."""
    payload = _payload(candidate_set, rebalance)
    payload["universe"][0]["name"] = "엉뚱한 이름"

    compiled = _parse(payload, candidate_set)

    assert compiled.spec.universe[0].name == candidate_set.candidates[0].name
