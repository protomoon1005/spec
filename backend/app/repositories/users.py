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


def insert_user(email: str, password_hash: str, role: str) -> UserRecord | None:
    """계정을 만든다. 이메일이 이미 있으면 None 을 돌려준다(라우터가 409로 바꾼다).

    중복 판정을 "조회 후 INSERT" 로 하지 않는 이유: 두 요청이 동시에 들어오면
    둘 다 조회를 통과하고 둘 다 INSERT 한다. uq_users_email 제약이 그 경합을
    막는 유일한 지점이라, ON CONFLICT 로 DB 에 판정을 맡긴다.
    """
    with get_engine().begin() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO users (email, password_hash, role)
                VALUES (:email, :password_hash, :role)
                ON CONFLICT (email) DO NOTHING
                RETURNING user_id, email, password_hash, role
                """
            ),
            {"email": email, "password_hash": password_hash, "role": role},
        ).one_or_none()
    return UserRecord(**row._mapping) if row is not None else None


def get_user_by_id(user_id: int) -> UserRecord | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT user_id, email, password_hash, role FROM users WHERE user_id = :user_id"),
            {"user_id": user_id},
        ).one_or_none()
    return UserRecord(**row._mapping) if row is not None else None
