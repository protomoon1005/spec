"""hypertables

Revision ID: 003_hypertables
Revises: 002_timeseries
Create Date: 2026-09-08 23:44:45.407347

price_daily/feature_store/macro_indicators만 하이퍼테이블로 전환한다. PK/컬럼/인덱스는
002_timeseries가 이미 만들어 뒀다.

세 PK가 파티션 컬럼(trade_date/as_of)을 포함하는지는 실행 중인 postgres 컨테이너에
직접 만들어서 테스트했다 — 충돌 없음 (TimescaleDB는 파티션 컬럼이 PK에 "포함"되면
되고 맨 앞일 필요는 없다).

downgrade: TimescaleDB 커뮤니티판에는 "하이퍼테이블 되돌리기" 네이티브 함수가 없다
(pg_proc에 %hypertable%remove%, %undo%, %revert% 전부 0건 확인). 표준 우회법인
CREATE TABLE ... LIKE ... INCLUDING ALL → 데이터 복사 → 원본 DROP CASCADE → rename
을 써서 평범한 테이블로 되돌린다. 이 패턴도 실제로 데이터 3행을 넣고 테스트해서
데이터·PK·인덱스가 보존되고 timescaledb_information.hypertables에서 빠지는 것까지
확인했다. (실제 라운드트립 테스트에서는 이 downgrade 직후 002의 downgrade가 테이블
자체를 CASCADE로 지우므로 데이터 보존이 실전에서 검증되는 지점은 아니지만, 이 리비전만
단독으로 롤백하는 경우를 위해 데이터를 보존하는 방식으로 짰다.)
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '003_hypertables'
down_revision: Union[str, Sequence[str], None] = '002_timeseries'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_HYPERTABLES: list[tuple[str, str]] = [
    ("price_daily", "trade_date"),
    ("feature_store", "as_of"),
    ("macro_indicators", "as_of"),
]


def upgrade() -> None:
    for table, time_column in _HYPERTABLES:
        op.execute(
            f"SELECT create_hypertable('{table}', '{time_column}', if_not_exists => TRUE)"
        )


def downgrade() -> None:
    for table, _time_column in reversed(_HYPERTABLES):
        plain = f"{table}__plain"
        op.execute(f"CREATE TABLE {plain} (LIKE {table} INCLUDING ALL)")
        op.execute(f"INSERT INTO {plain} SELECT * FROM {table}")
        op.execute(f"DROP TABLE {table} CASCADE")
        op.execute(f"ALTER TABLE {plain} RENAME TO {table}")
