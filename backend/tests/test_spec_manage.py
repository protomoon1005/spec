"""내 전략서 목록과 draft 삭제 — GET /specs, DELETE /specs/{spec_id}.

postgres 와 시드가 적재된 상태여야 한다. 전략서는 LLM 없이 후처리 저장으로 만든다.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.m1 import candidates as C
from app.m1 import postprocess
from app.repositories import bbl, profiles, specs


@pytest.fixture
def owner(engine, make_user):
    """성향이 확정된 사용자 하나. 테스트가 만든 전략서까지 뒤에서 지운다."""
    email, user_id = make_user("retail")
    version = profiles.active_preset_version()
    record = profiles.insert_profile(
        user_id=user_id,
        risk_level=5,
        preset_version=version,
        defaults=profiles.get_profile_defaults(version, 5),
        provenance={},
    )
    yield email, record
    with engine.begin() as conn:
        mine = "(SELECT spec_id FROM strategy_specs WHERE user_id = :u)"
        for table in ("backtest_runs", "spec_universe", "validation_logs"):
            conn.execute(text(f"DELETE FROM {table} WHERE spec_id IN {mine}"), {"u": user_id})
        conn.execute(text("DELETE FROM strategy_specs WHERE user_id = :u"), {"u": user_id})
        conn.execute(text("DELETE FROM risk_profiles WHERE user_id = :u"), {"u": user_id})


def _save_spec(profile) -> str:
    candidate_set = C.select(risk_level=5, target=3)
    payload = {
        "spec_id": "무시됨",
        "spec_version": "0.1",
        "user_id": 999,
        "name": "관리 테스트 전략",
        "created_at": "2026-09-20T09:00:00+09:00",
        "universe": [
            {"ticker": c.ticker, "name": c.name, "weight_min": c.weight_min, "weight_max": c.weight_max}
            for c in candidate_set.candidates
        ],
        "rebalance": bbl.get_block("RB_MONTHLY_FIRST").params_schema,
        "signal_rules": {"market_analysis": {"indicators": ["ret_20"]}},
        "constraint": {
            "max_weight_per_asset": 0.30,
            "min_weight_per_asset": 0.00,
            "cash_min": 0.05,
            "max_loss_per_trade": 0.05,
            "max_drawdown": 0.25,
        },
    }
    compiled = postprocess.parse(payload, candidate_set, spec_id=specs.new_spec_id(), user_id=profile.user_id)
    return postprocess.save(
        compiled, user_id=profile.user_id, profile_id=profile.profile_id, input_prompt="x"
    ).spec_id


def _auth(client, username: str) -> dict:
    token = client.post("/auth/login", json={"username": username}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_list_shows_only_my_specs(engine, client, owner, make_user):
    email, profile = owner
    spec_id = _save_spec(profile)
    other, _ = make_user("retail")

    mine = client.get("/specs", headers=_auth(client, email))
    theirs = client.get("/specs", headers=_auth(client, other))

    assert mine.status_code == 200
    assert [(row["spec_id"], row["status"], row["universe_size"]) for row in mine.json()] == [
        (spec_id, "draft", 3)
    ]
    assert theirs.json() == []


def test_delete_draft_removes_spec_and_universe(engine, client, owner):
    email, profile = owner
    spec_id = _save_spec(profile)
    headers = _auth(client, email)

    resp = client.delete(f"/specs/{spec_id}", headers=headers)

    assert resp.status_code == 204
    assert client.get(f"/specs/{spec_id}", headers=headers).status_code == 404
    assert specs.get_spec_universe(spec_id) == []


def test_cannot_delete_someone_elses_spec(engine, client, owner, make_user):
    _, profile = owner
    spec_id = _save_spec(profile)
    other, _ = make_user("retail")

    resp = client.delete(f"/specs/{spec_id}", headers=_auth(client, other))

    assert resp.status_code == 404
    assert specs.get_spec(spec_id) is not None


def test_approved_spec_is_not_deleted(engine, client, owner):
    email, profile = owner
    spec_id = _save_spec(profile)
    with engine.begin() as conn:
        conn.execute(text("UPDATE strategy_specs SET status = 'approved' WHERE spec_id = :s"), {"s": spec_id})

    resp = client.delete(f"/specs/{spec_id}", headers=_auth(client, email))

    assert resp.status_code == 409
    assert specs.get_spec(spec_id) is not None


def test_spec_with_backtest_record_is_not_deleted(engine, client, owner):
    email, profile = owner
    spec_id = _save_spec(profile)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO backtest_runs (spec_id) VALUES (:s)"), {"s": spec_id})

    resp = client.delete(f"/specs/{spec_id}", headers=_auth(client, email))

    assert resp.status_code == 409
    assert specs.get_spec(spec_id) is not None


def test_spec_risk_level_is_the_one_at_creation(engine, owner):
    # 재진단해 성향이 바뀌어도 전략서는 만들 때 참조한 성향을 본다.
    _email, profile = owner
    spec_id = _save_spec(profile)
    profiles.insert_profile(
        user_id=profile.user_id,
        risk_level=2,
        preset_version=profile.preset_version,
        defaults=profiles.get_profile_defaults(profile.preset_version, 2),
        provenance={},
    )

    assert specs.get_spec_risk_level(spec_id) == 5
    assert specs.get_spec_risk_level("STR-없는전략서") is None
