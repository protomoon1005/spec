"""내 전략서 목록과 draft 삭제 — GET /specs, DELETE /specs/{strategy_id}.

postgres 와 시드가 적재된 상태여야 한다. 전략서는 LLM 없이 v0.2 저장 함수로 만든다.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.repositories import specs


@pytest.fixture
def owner(engine, make_user):
    """사용자 하나 (email, user_id). 테스트가 만든 전략서까지 뒤에서 지운다."""
    email, user_id = make_user()
    yield email, user_id
    with engine.begin() as conn:
        mine = "(SELECT strategy_id FROM strategies WHERE user_id = :u)"
        for table in ("backtest_runs", "strategy_assets", "validation_logs"):
            conn.execute(text(f"DELETE FROM {table} WHERE strategy_id IN {mine}"), {"u": user_id})
        conn.execute(text("DELETE FROM strategies WHERE user_id = :u"), {"u": user_id})


SPEC = {
    "universe": {"tickers": ["005930", "069500", "148070"]},
    "weights": "equal",
    "rebalance": "RB_MONTHLY_FIRST",
    "rules": [],
    "constraint": {},
    "backtest": {"start": "2023-01-01", "end": "2025-12-31", "capital": 10000000, "include_dividends": False},
}


def _save_spec(user_id: int) -> str:
    return specs.insert_strategy_v0_2(
        strategy_id=specs.new_strategy_id(),
        user_id=user_id,
        name="관리 테스트 전략",
        input_prompt="x",
        spec=SPEC,
        asset_weights=[(t, 1 / 3) for t in SPEC["universe"]["tickers"]],
        logs=[{"stage": 1, "passed": True, "violations": []}],
    ).strategy_id


def _auth(client, username: str) -> dict:
    token = client.post("/auth/login", json={"username": username}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_list_shows_only_my_specs(engine, client, owner, make_user):
    email, user_id = owner
    strategy_id = _save_spec(user_id)
    other, _ = make_user()

    mine = client.get("/specs", headers=_auth(client, email))
    theirs = client.get("/specs", headers=_auth(client, other))

    assert mine.status_code == 200
    assert [(row["strategy_id"], row["status"], row["universe_size"]) for row in mine.json()] == [
        (strategy_id, "draft", 3)
    ]
    assert theirs.json() == []


def test_delete_draft_removes_spec_and_universe(engine, client, owner):
    email, user_id = owner
    strategy_id = _save_spec(user_id)
    headers = _auth(client, email)

    resp = client.delete(f"/specs/{strategy_id}", headers=headers)

    assert resp.status_code == 204
    assert specs.get_strategy_v0_2(strategy_id) is None
    assert client.get("/specs", headers=headers).json() == []


def test_cannot_delete_someone_elses_spec(engine, client, owner, make_user):
    _, user_id = owner
    strategy_id = _save_spec(user_id)
    other, _ = make_user()

    resp = client.delete(f"/specs/{strategy_id}", headers=_auth(client, other))

    assert resp.status_code == 404
    assert specs.get_strategy_v0_2(strategy_id) is not None


def test_approved_spec_is_not_deleted(engine, client, owner):
    email, user_id = owner
    strategy_id = _save_spec(user_id)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE strategies SET status = 'approved' WHERE strategy_id = :s"), {"s": strategy_id}
        )

    resp = client.delete(f"/specs/{strategy_id}", headers=_auth(client, email))

    assert resp.status_code == 409
    assert specs.get_strategy_v0_2(strategy_id) is not None


def test_spec_with_backtest_record_is_not_deleted(engine, client, owner):
    email, user_id = owner
    strategy_id = _save_spec(user_id)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO backtest_runs (strategy_id) VALUES (:s)"), {"s": strategy_id})

    resp = client.delete(f"/specs/{strategy_id}", headers=_auth(client, email))

    assert resp.status_code == 409
    assert specs.get_strategy_v0_2(strategy_id) is not None
