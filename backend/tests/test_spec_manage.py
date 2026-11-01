"""내 전략서 목록과 draft 삭제 — GET /specs, DELETE /specs/{spec_id}.

postgres 와 시드가 적재된 상태여야 한다. 전략서는 LLM 없이 후처리 저장으로 만든다.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.m1 import candidates as C
from app.m1 import postprocess
from app.repositories import bbl, specs


@pytest.fixture
def owner(engine, make_user):
    """사용자 하나 (email, user_id). 테스트가 만든 전략서까지 뒤에서 지운다."""
    email, user_id = make_user()
    yield email, user_id
    with engine.begin() as conn:
        mine = "(SELECT spec_id FROM strategy_specs WHERE user_id = :u)"
        for table in ("backtest_runs", "spec_universe", "validation_logs"):
            conn.execute(text(f"DELETE FROM {table} WHERE spec_id IN {mine}"), {"u": user_id})
        conn.execute(text("DELETE FROM strategy_specs WHERE user_id = :u"), {"u": user_id})


def _save_spec(user_id: int) -> str:
    candidate_set = C.select(target=3)
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
    compiled = postprocess.parse(payload, candidate_set, spec_id=specs.new_spec_id(), user_id=user_id)
    return postprocess.save(compiled, user_id=user_id, input_prompt="x").spec_id


def _auth(client, username: str) -> dict:
    token = client.post("/auth/login", json={"username": username}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_list_shows_only_my_specs(engine, client, owner, make_user):
    email, user_id = owner
    spec_id = _save_spec(user_id)
    other, _ = make_user()

    mine = client.get("/specs", headers=_auth(client, email))
    theirs = client.get("/specs", headers=_auth(client, other))

    assert mine.status_code == 200
    assert [(row["spec_id"], row["status"], row["universe_size"]) for row in mine.json()] == [
        (spec_id, "draft", 3)
    ]
    assert theirs.json() == []


def test_delete_draft_removes_spec_and_universe(engine, client, owner):
    email, user_id = owner
    spec_id = _save_spec(user_id)
    headers = _auth(client, email)

    resp = client.delete(f"/specs/{spec_id}", headers=headers)

    assert resp.status_code == 204
    assert client.get(f"/specs/{spec_id}", headers=headers).status_code == 404
    assert specs.get_spec_universe(spec_id) == []


def test_cannot_delete_someone_elses_spec(engine, client, owner, make_user):
    _, user_id = owner
    spec_id = _save_spec(user_id)
    other, _ = make_user()

    resp = client.delete(f"/specs/{spec_id}", headers=_auth(client, other))

    assert resp.status_code == 404
    assert specs.get_spec(spec_id) is not None


def test_approved_spec_is_not_deleted(engine, client, owner):
    email, user_id = owner
    spec_id = _save_spec(user_id)
    with engine.begin() as conn:
        conn.execute(text("UPDATE strategy_specs SET status = 'approved' WHERE spec_id = :s"), {"s": spec_id})

    resp = client.delete(f"/specs/{spec_id}", headers=_auth(client, email))

    assert resp.status_code == 409
    assert specs.get_spec(spec_id) is not None


def test_spec_with_backtest_record_is_not_deleted(engine, client, owner):
    email, user_id = owner
    spec_id = _save_spec(user_id)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO backtest_runs (spec_id) VALUES (:s)"), {"s": spec_id})

    resp = client.delete(f"/specs/{spec_id}", headers=_auth(client, email))

    assert resp.status_code == 409
    assert specs.get_spec(spec_id) is not None


def test_detail_carries_raw_and_final_range(engine, client, owner):
    """전략서 보기는 AI 원래 범위와 확정 범위를 같이 준다. 검증 전이라 둘이 같다."""
    email, user_id = owner
    spec_id = _save_spec(user_id)

    body = client.get(f"/specs/{spec_id}", headers=_auth(client, email)).json()

    assert len(body["universe"]) == 3
    for item in body["universe"]:
        assert item["weight_min_raw"] == item["weight_min"]
        assert item["weight_max_raw"] == item["weight_max"]
        assert 0 <= item["weight_min_raw"] <= item["weight_max_raw"] <= 1
