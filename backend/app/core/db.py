"""DB 엔진. DATABASE_URL 환경변수로만 접속 정보를 받는다.

app/repositories/ 만 이 모듈을 통해 DB에 접근한다. 다른 계층은 이 모듈이 아니라
repositories/의 as_of 질의 함수를 거쳐야 한다 (docs/infra-spec.md 5단계).
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Connection, Engine, create_engine


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL 환경변수가 없다. "
            "예) DATABASE_URL=postgresql+psycopg://spec:spec_dev_password@localhost:5432/spec"
        )
    return create_engine(database_url, pool_pre_ping=True)


@contextmanager
def reading(conn: Connection | None = None) -> Iterator[Connection]:
    """읽기용 연결. conn 을 주면 그 연결(바깥 트랜잭션)을 그대로 쓰고 닫지 않는다.

    행 잠금을 쥔 트랜잭션 안에서 저장소 읽기 함수를 부를 때 쓴다. 따로 풀에서 연결을
    빌리면, 같은 잠금을 기다리는 요청들이 풀을 다 잡고 있을 때 잠금을 쥔 쪽이 연결을
    못 얻어 모두가 풀 대기 시간(30초)까지 멈춘다(2026-10-21 리뷰, 동시 승인 20건 재현).
    """
    if conn is not None:
        yield conn
        return
    with get_engine().connect() as own:
        yield own
