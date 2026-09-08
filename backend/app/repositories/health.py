"""헬스체크 전용 최소 조회. app/core/db.py는 repositories/ 밖에서 직접 쓰지
않는다는 규약을 헬스체크 라우터도 지키기 위한 얇은 래퍼다."""
from __future__ import annotations

from sqlalchemy import text

from app.core.db import get_engine


def ping_database() -> bool:
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))
    return True
