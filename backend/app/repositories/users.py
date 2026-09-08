"""users 조회. as_of 규약 대상은 아니지만(시점 자산이 아니라 계정 레코드다),
app/core/db.py의 규약("app/repositories/ 만 이 모듈을 통해 DB에 접근한다")을
지키기 위해 인증 라우터도 이 리포지토리를 거친다."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text

from app.core.db import get_engine


@dataclass(frozen=True)
class UserRecord:
    user_id: int
    email: str
    password_hash: str
    role: str


def get_user_by_email(email: str) -> UserRecord | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT user_id, email, password_hash, role FROM users WHERE email = :email"),
            {"email": email},
        ).one_or_none()
    return UserRecord(**row._mapping) if row is not None else None


def get_user_by_id(user_id: int) -> UserRecord | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT user_id, email, password_hash, role FROM users WHERE user_id = :user_id"),
            {"user_id": user_id},
        ).one_or_none()
    return UserRecord(**row._mapping) if row is not None else None
