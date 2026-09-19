#!/usr/bin/env python3
"""db/migrate/*.sql 를 파일명 순서대로 적용하고 schema_migrations 에 이력을 남긴다.

db/init/ 은 postgres 컨테이너 **최초 기동 시에만** 실행되는 빈 DB 부트스트랩이다.
그래서 지금까지 스키마를 바꾸려면 `docker compose down -v` 로 볼륨을 지우고 다시
띄우는 수밖에 없었다 — price_daily·macro_indicators·regime_snapshots 등 이미
쌓인 데이터가 전부 사라진다는 뜻이다. 이 스크립트는 down -v 없이 증분 SQL
파일로 스키마를 바꾸는 얇은 경로다.

**의도적으로 얇다.** Alembic 같은 리비전 그래프·다운그레이드·자동 생성은 없다.
파일명 순서대로 한 번씩 적용하고 이력만 남긴다. db/init/ 은 이 스크립트가
건드리지 않는다 — 빈 DB 부트스트랩 전용으로 남겨 둔다.

마이그레이션 파일은 `db/migrate/NNN_설명.sql` (3자리 순번 + 설명) 형식으로 추가한다.
파일 하나가 하나의 DB 트랜잭션으로 적용된다 — 트랜잭션을 못 타는 문장
(`CREATE INDEX CONCURRENTLY` 등)은 이 스크립트로 넣지 않는다.

api 컨테이너 안에서 실행하는 것이 표준 경로다 (호스트 python에는 sqlalchemy가
없다 — scripts/load_etf_master.py 와 동일한 이유):
    docker compose exec api python /repo/scripts/apply_migrations.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MIGRATE_DIR = REPO_ROOT / "db" / "migrate"


def _database_url() -> str:
    return os.environ.get(
        "DATABASE_URL", "postgresql+psycopg://spec:spec_dev_password@localhost:5432/spec"
    )


def _ensure_migrations_table(conn) -> None:
    from sqlalchemy import text

    conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version text PRIMARY KEY,"
            " applied_at timestamptz NOT NULL DEFAULT now()"
            ")"
        )
    )


def _applied_versions(conn) -> set[str]:
    from sqlalchemy import text

    rows = conn.execute(text("SELECT version FROM schema_migrations")).fetchall()
    return {row[0] for row in rows}


def _apply_file(engine, path: Path) -> None:
    """path 전체를 하나의 트랜잭션으로 실행하고 schema_migrations 에 기록한다.

    sqlalchemy text()의 바인드 파라미터 파싱(`:name`)을 피하려고 raw DBAPI
    커넥션(psycopg)으로 직접 실행한다 — 마이그레이션 SQL에 `::type` 캐스트나
    콜론이 들어가도 안전하다.
    """
    sql = path.read_text(encoding="utf-8")
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        cur.execute(sql)
        cur.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.name,))
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()


def main() -> int:
    import argparse

    from sqlalchemy import create_engine

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--migrate-dir", type=Path, default=DEFAULT_MIGRATE_DIR)
    args = parser.parse_args()

    files = sorted(args.migrate_dir.glob("*.sql"))

    engine = create_engine(_database_url(), pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            _ensure_migrations_table(conn)
            applied = _applied_versions(conn)

        pending = [path for path in files if path.name not in applied]
        if not pending:
            print("apply_migrations: 적용할 새 마이그레이션 없음 (멱등)")
            return 0

        applied_now = []
        for path in pending:
            try:
                _apply_file(engine, path)
            except Exception as exc:
                print(f"apply_migrations: {path.name} 적용 실패 — {exc}", file=sys.stderr)
                if applied_now:
                    done = ", ".join(applied_now)
                    print(f"apply_migrations: 이전까지 적용됨: {done}", file=sys.stderr)
                return 1
            applied_now.append(path.name)
            print(f"apply_migrations: {path.name} 적용 완료")

        print(f"apply_migrations: {len(applied_now)}건 적용 ({', '.join(applied_now)})")
    finally:
        engine.dispose()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
