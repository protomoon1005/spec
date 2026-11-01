"""JWT 발급/검증과 로그인 사용자 의존성.

사용자는 일반 투자자 한 종류뿐이라 역할 구분이 없다. 토큰이 없거나 잘못되면 401 이다.
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

_bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


class TokenPayload(BaseModel):
    sub: str  # user_id, 문자열로 인코딩
    type: Literal["access", "refresh"]
    exp: dt.datetime
    iat: dt.datetime


def _encode(user_id: int, token_type: Literal["access", "refresh"], ttl_minutes: int) -> str:
    settings = get_settings()
    now = dt.datetime.now(dt.UTC)
    payload = {
        "sub": str(user_id),
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + dt.timedelta(minutes=ttl_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: int) -> str:
    settings = get_settings()
    return _encode(user_id, "access", settings.jwt_access_ttl_minutes)


def create_refresh_token(user_id: int) -> str:
    settings = get_settings()
    return _encode(user_id, "refresh", settings.jwt_refresh_ttl_minutes)


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
    return AuthUser(user_id=int(payload["sub"]))


# 로그인한 사용자면 통과한다. 라우터는 이 이름으로 붙는다.
require_user = get_current_user
