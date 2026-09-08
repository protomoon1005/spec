"""POST /auth/login, POST /auth/refresh — JWT 발급 (docs/infra-spec.md 7단계).

다른 세 라인이 여기 토큰으로 다른 라우터에 붙으므로 이 두 엔드포인트는
501이 아니라 실제로 동작한다. 회원가입/사용자 생성은 이번 단계 범위가
아니다 — users 행은 시드나 관리자 경로로 이미 존재한다고 가정한다.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.core.security import Role, create_access_token, create_refresh_token, decode_token, verify_password
from app.repositories.users import get_user_by_email, get_user_by_id

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest) -> TokenResponse:
    user = get_user_by_email(payload.email)
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="이메일 또는 비밀번호가 올바르지 않다",
        )
    role: Role = user.role  # type: ignore[assignment] — DB CHECK 제약이 retail/pro/admin만 허용한다
    return TokenResponse(
        access_token=create_access_token(user.user_id, role),
        refresh_token=create_refresh_token(user.user_id, role),
    )


@router.post("/refresh", response_model=AccessTokenResponse)
def refresh(payload: RefreshRequest) -> AccessTokenResponse:
    claims = decode_token(payload.refresh_token)
    if claims.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="refresh 토큰이 아니다",
        )
    user = get_user_by_id(int(claims["sub"]))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="사용자를 찾을 수 없다")
    role: Role = user.role  # type: ignore[assignment]
    return AccessTokenResponse(access_token=create_access_token(user.user_id, role))
