"""triggers

Revision ID: 004_triggers
Revises: 003_hypertables
Create Date: 2026-09-08 23:44:46.011293

두 가지 불변성만 건다 (docs/db-erd.md 4.3 "Spec 불변" · "가중치 이력 보존"):

1. strategy_specs / spec_universe — status='approved'가 된 뒤로는 UPDATE를 막는다.
   spec_universe에는 자체 status가 없어서 상위 strategy_specs.status를 조회해서 판단한다.
   범위 참고: 이 트리거는 "status가 정확히 approved인 행의 UPDATE"만 literal하게 막는다.
   approved -> running -> closed 로 status 자체가 넘어가는 절차를 어떻게 할지는
   (트리거를 우회하는 별도 권한 경로가 필요한지 등) 이번 단계 범위 밖이라 손대지 않았다.
2. view_weights — append-only. 모든 UPDATE를 무조건 거부한다.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '004_triggers'
down_revision: Union[str, Sequence[str], None] = '003_hypertables'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_UPGRADE_STATEMENTS: list[str] = [
    """
    CREATE FUNCTION fn_strategy_specs_block_update() RETURNS trigger AS $$
    BEGIN
        IF OLD.status = 'approved' THEN
            RAISE EXCEPTION 'strategy_specs: approved 상태인 spec_id=% 는 수정할 수 없다. 새 spec_version을 발행하라', OLD.spec_id;
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER trg_strategy_specs_block_update
        BEFORE UPDATE ON strategy_specs
        FOR EACH ROW EXECUTE FUNCTION fn_strategy_specs_block_update()
    """,
    """
    CREATE FUNCTION fn_spec_universe_block_update() RETURNS trigger AS $$
    DECLARE
        parent_status VARCHAR;
    BEGIN
        SELECT status INTO parent_status FROM strategy_specs WHERE spec_id = OLD.spec_id;
        IF parent_status = 'approved' THEN
            RAISE EXCEPTION 'spec_universe: 상위 spec_id=% 가 approved 상태라 수정할 수 없다', OLD.spec_id;
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER trg_spec_universe_block_update
        BEFORE UPDATE ON spec_universe
        FOR EACH ROW EXECUTE FUNCTION fn_spec_universe_block_update()
    """,
    """
    CREATE FUNCTION fn_view_weights_block_update() RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'view_weights: append-only 테이블이라 UPDATE할 수 없다 (view_weight_id=%). as_of를 갱신해 새 행을 추가하라', OLD.view_weight_id;
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER trg_view_weights_block_update
        BEFORE UPDATE ON view_weights
        FOR EACH ROW EXECUTE FUNCTION fn_view_weights_block_update()
    """,
]

_DOWNGRADE_STATEMENTS: list[str] = [
    "DROP TRIGGER IF EXISTS trg_view_weights_block_update ON view_weights",
    "DROP FUNCTION IF EXISTS fn_view_weights_block_update()",
    "DROP TRIGGER IF EXISTS trg_spec_universe_block_update ON spec_universe",
    "DROP FUNCTION IF EXISTS fn_spec_universe_block_update()",
    "DROP TRIGGER IF EXISTS trg_strategy_specs_block_update ON strategy_specs",
    "DROP FUNCTION IF EXISTS fn_strategy_specs_block_update()",
]


def upgrade() -> None:
    for stmt in _UPGRADE_STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in _DOWNGRADE_STATEMENTS:
        op.execute(stmt)
