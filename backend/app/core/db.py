"""DB 엔진. DATABASE_URL 환경변수로만 접속 정보를 받는다 (db/migrations/env.py와 동일 규약).

app/repositories/ 만 이 모듈을 통해 DB에 접근한다. 다른 계층은 이 모듈이 아니라
repositories/의 as_of 질의 함수를 거쳐야 한다 (docs/infra-spec.md 5단계).
"""
from __future__ import annotations

import os
from functools import lru_cache

from sqlalchemy import Engine, create_engine


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL 환경변수가 없다. "
            "예) DATABASE_URL=postgresql+psycopg://spec:spec_dev_password@localhost:5432/spec"
        )
    return create_engine(database_url, pool_pre_ping=True)
