"""승인한 전략서는 상태 칸만 앞으로 간다 — db/migrate/003_spec_status_transition.sql.

내용(종목 · 비중 · 규칙 · 제약)은 승인 뒤 어떤 상태에서도 못 고치고, 상태는
approved → running → closed 순서로 한 칸씩만 간다.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

MONTHLY = {"trigger": {"type": "calendar", "freq": "monthly", "day": 1}, "min_interval_days": 20}
ONE = [("069500", 0.05, 0.30)]


@pytest.fixture
def spec_in(engine, make_draft_spec):
    """(status) -> spec_id. draft 로 만든 뒤 그 상태까지 순서대로 올린다."""
    order = ["draft", "approved", "running", "closed"]

    def _make(status: str) -> str:
        spec_id = make_draft_spec(
            ONE, rebalance=MONTHLY, name="상태 전환 테스트", signal_rules={}, constraint={}
        )
        for step in order[1 : order.index(status) + 1]:
            _set_status(engine, spec_id, step)
        return spec_id

    return _make


def _set_status(engine, spec_id: str, status: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE strategy_specs SET status = :st WHERE spec_id = :s"), {"st": status, "s": spec_id}
        )


def _status(engine, spec_id: str) -> str:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT status FROM strategy_specs WHERE spec_id = :s"), {"s": spec_id}
        ).scalar_one()


def test_status_moves_forward_one_step_at_a_time(engine, spec_in):
    spec_id = spec_in("approved")

    _set_status(engine, spec_id, "running")
    assert _status(engine, spec_id) == "running"
    _set_status(engine, spec_id, "closed")
    assert _status(engine, spec_id) == "closed"


@pytest.mark.parametrize(
    ("start", "target"),
    [("approved", "draft"), ("approved", "closed"), ("running", "approved"), ("running", "draft"),
     ("closed", "running")],
)
def test_status_cannot_skip_or_go_back(engine, spec_in, start, target):
    spec_id = spec_in(start)

    with pytest.raises(DBAPIError, match="순서로만"):
        _set_status(engine, spec_id, target)
    assert _status(engine, spec_id) == start


@pytest.mark.parametrize("status", ["approved", "running", "closed"])
def test_content_stays_frozen_in_every_approved_state(engine, spec_in, status):
    spec_id = spec_in(status)

    with pytest.raises(DBAPIError, match="상태 말고는"), engine.begin() as conn:
        conn.execute(text("UPDATE strategy_specs SET name = '바꾼 이름' WHERE spec_id = :s"), {"s": spec_id})
    # 상태와 내용을 한 번에 바꾸는 것도 막는다
    if status == "approved":
        with pytest.raises(DBAPIError, match="상태 말고는"), engine.begin() as conn:
            conn.execute(
                text("UPDATE strategy_specs SET status = 'running', name = 'x' WHERE spec_id = :s"),
                {"s": spec_id},
            )
    # running 으로 넘긴 뒤에도 종목 · 비중이 다시 열리지 않는다
    with pytest.raises(DBAPIError, match="spec_universe"), engine.begin() as conn:
        conn.execute(text("UPDATE spec_universe SET weight_max = 1 WHERE spec_id = :s"), {"s": spec_id})
    assert _status(engine, spec_id) == status


def test_draft_is_still_editable(engine, spec_in):
    spec_id = spec_in("draft")

    with engine.begin() as conn:
        conn.execute(text("UPDATE strategy_specs SET name = '고친 이름' WHERE spec_id = :s"), {"s": spec_id})
        conn.execute(text("UPDATE spec_universe SET weight_max = 0.25 WHERE spec_id = :s"), {"s": spec_id})
