"""전략서 저장 · 목록 · 삭제.

시점 규약 대상이 아니다. 명세화는 전략서를 draft 로만 저장한다.
전략서 v0.2 는 strategies.spec 에 통째로 둔다(db/migrate/003).
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from sqlalchemy import Connection, text

from app.core.db import get_engine, reading

STRATEGY_ID_PREFIX = "STR"
DRAFT = "draft"


@dataclass(frozen=True)
class SavedSpec:
    strategy_id: str
    user_id: int
    status: str
    universe_size: int


def new_strategy_id() -> str:
    return f"{STRATEGY_ID_PREFIX}-{uuid.uuid4().hex[:12]}"


def latest_validation(strategy_id: str, *, conn: Connection | None = None) -> list[dict]:
    """가장 최근 검증 한 번의 단계별 기록. 단계 순서대로. 검증한 적이 없으면 빈 목록.

    insert_strategy_v0_2 는 한 번의 검사를 한 트랜잭션으로 넣으므로 그 행들의 checked_at
    (트랜잭션 시작 시각)이 같다. 가장 늦은 checked_at 의 행들이 마지막 검증이다.

    conn 을 주면 그 연결(승인 트랜잭션)에서 읽는다(core.db.reading).
    """
    with reading(conn) as conn:
        rows = conn.execute(
            text(
                """
                SELECT stage, passed, violations, adjusted_bounds, clamped_fields, checked_at
                  FROM validation_logs
                 WHERE strategy_id = :strategy_id
                   AND checked_at = (
                       SELECT max(checked_at) FROM validation_logs WHERE strategy_id = :strategy_id
                   )
                 ORDER BY stage, validation_id
                """
            ),
            {"strategy_id": strategy_id},
        ).all()
    return [dict(row._mapping) for row in rows]


def list_specs(user_id: int) -> list[dict]:
    """사용자의 전략서 목록. 최근 것부터, 종목 수를 곁들인다."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT s.strategy_id, s.name, s.status, s.created_at,
                       (SELECT count(*) FROM strategy_assets u
                         WHERE u.strategy_id = s.strategy_id) AS universe_size
                  FROM strategies s
                 WHERE s.user_id = :user_id
                 ORDER BY s.created_at DESC
                """
            ),
            {"user_id": user_id},
        ).all()
    return [dict(row._mapping) for row in rows]


# draft 전략서가 지워질 때 함께 지우는 하위 기록. 백테스트 기록이 붙은 전략서는 지우지 않는다.
# 실제로 돌려 본 흔적이라서다.
_OWNED_BY_DRAFT = ("strategy_assets", "validation_logs")
_BLOCKS_DELETE = ("backtest_runs",)


def insert_strategy_v0_2(
    *,
    strategy_id: str,
    user_id: int,
    name: str,
    input_prompt: str | None,
    spec: dict,
    asset_weights: list[tuple[str, float | None]],
    logs: list[dict],
) -> SavedSpec:
    """전략서 v0.2 한 건을 draft 로 저장한다(docs/v0.2-plan.md 5단계, A안).

    전략서는 strategies.spec 에 통째로 넣는다(db/migrate/003). 옛 칸(rebalance · signal_rules ·
    constraint_user)은 db/migrate/004 에서 지웠다.
    strategy_assets 에는 종목과 자리 비율을 weight_min = weight_max 로 남긴다
    (종목 목록을 다른 화면과 저장소가 계속 같은 곳에서 읽게). logs 는 검사 1~4단계 결과로 단계마다 한 행이다
    (stage · passed · violations). 4단계(근거 검사)는 전략서가 저장되기 전에 돌아서 여기서 같이 남긴다.
    """
    with get_engine().begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO strategies
                    (strategy_id, user_id, strategy_version, name, input_prompt, spec, status)
                VALUES
                    (:strategy_id, :user_id, '0.2', :name, :input_prompt, CAST(:spec AS jsonb), :status)
                """
            ),
            {"strategy_id": strategy_id, "user_id": user_id, "name": name, "input_prompt": input_prompt,
             "spec": _dump(spec), "status": DRAFT},
        )
        conn.execute(
            text(
                """
                INSERT INTO strategy_assets (strategy_id, ticker, weight_min, weight_max)
                VALUES (:strategy_id, :ticker, :weight, :weight)
                """
            ),
            [{"strategy_id": strategy_id, "ticker": t, "weight": w} for t, w in asset_weights],
        )
        conn.execute(
            text(
                """
                INSERT INTO validation_logs (strategy_id, stage, passed, violations)
                VALUES (:strategy_id, :stage, :passed, CAST(:violations AS jsonb))
                """
            ),
            [{"strategy_id": strategy_id, "stage": log["stage"], "passed": log["passed"],
              "violations": _dump(log["violations"])} for log in logs],
        )
    return SavedSpec(strategy_id=strategy_id, user_id=user_id, status=DRAFT, universe_size=len(asset_weights))


def get_strategy_v0_2(strategy_id: str) -> dict | None:
    """v0.2 전략서(strategies.spec)와 주인 · 이름 · 상태. v0.2 가 아니거나 없으면 None."""
    with reading() as conn:
        row = conn.execute(
            text(
                """
                SELECT strategy_id, user_id, name, status, input_prompt, spec
                  FROM strategies
                 WHERE strategy_id = :s AND strategy_version = '0.2'
                """
            ),
            {"s": strategy_id},
        ).one_or_none()
    return None if row is None else dict(row._mapping)


def delete_draft_spec(strategy_id: str, user_id: int) -> str:
    """draft 전략서를 하위 기록과 함께 한 트랜잭션으로 지운다.

    결과: "deleted" · "not_found"(없거나 남의 것) · "not_draft" · "has_records".
    FK 에 ON DELETE CASCADE 가 없으므로 하위 테이블을 먼저 지운다.
    """
    with get_engine().begin() as conn:
        row = conn.execute(
            text("SELECT user_id, status FROM strategies WHERE strategy_id = :s FOR UPDATE"),
            {"s": strategy_id},
        ).one_or_none()
        if row is None or row.user_id != user_id:
            return "not_found"
        if row.status != DRAFT:
            return "not_draft"
        for table in _BLOCKS_DELETE:
            found = conn.execute(
                text(f"SELECT 1 FROM {table} WHERE strategy_id = :s LIMIT 1"), {"s": strategy_id}
            )
            if found.first():
                return "has_records"
        for table in _OWNED_BY_DRAFT:
            conn.execute(text(f"DELETE FROM {table} WHERE strategy_id = :s"), {"s": strategy_id})
        conn.execute(text("DELETE FROM strategies WHERE strategy_id = :s"), {"s": strategy_id})
    return "deleted"


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _dump_or_none(value) -> str | None:
    return None if value is None else _dump(value)
