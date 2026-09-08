"""JWT 발급/검증 + 3역할(retail/pro/admin) RBAC 의존성.

docs/infra-spec.md 7단계: "JWT 액세스/리프레시 + retail·pro·admin 3역할 RBAC
의존성은 실제로 동작하게 만든다 (다른 라인이 여기 붙는다)". 라우터 본체는
501이어도 이 의존성 체인(토큰 없음 -> 401, 역할 불일치 -> 403)은 실제로 걸린다.
"""
from __future__ import annotations

import datetime as dt
from typing import Literal

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.core.config import get_settings

Role = Literal["retail", "pro", "admin"]
ROLES: tuple[Role, ...] = ("retail", "pro", "admin")

_bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


class TokenPayload(BaseModel):
    sub: str  # user_id, 문자열로 인코딩
    role: Role
    type: Literal["access", "refresh"]
    exp: dt.datetime
    iat: dt.datetime


def _encode(user_id: int, role: Role, token_type: Literal["access", "refresh"], ttl_minutes: int) -> str:
    settings = get_settings()
    now = dt.datetime.now(dt.UTC)
    payload = {
        "sub": str(user_id),
        "role": role,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + dt.timedelta(minutes=ttl_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: int, role: Role) -> str:
    settings = get_settings()
    return _encode(user_id, role, "access", settings.jwt_access_ttl_minutes)


def create_refresh_token(user_id: int, role: Role) -> str:
    settings = get_settings()
    return _encode(user_id, role, "refresh", settings.jwt_refresh_ttl_minutes)


def decode_token(token: str) -> dict:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="유효하지 않거나 만료된 토큰이다",
        ) from exc


class AuthUser(BaseModel):
    user_id: int
    role: Role


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> AuthUser:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization: Bearer <token> 헤더가 필요하다",
        )
    payload = decode_token(credentials.credentials)
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="access 토큰이 아니다",
        )
    return AuthUser(user_id=int(payload["sub"]), role=payload["role"])


def require_roles(*allowed_roles: Role):
    """지정한 역할만 통과시키는 FastAPI 의존성 팩토리. 나머지는 403."""

    def _dependency(user: AuthUser = Depends(get_current_user)) -> AuthUser:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"role '{user.role}' 는 이 엔드포인트에 허용되지 않는다",
            )
        return user

    return _dependency


require_any_role = require_roles(*ROLES)
require_admin = require_roles("admin")
