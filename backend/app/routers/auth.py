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


@router.post("/login", response_model=TokenResponse, summary="로그인 — 토큰 발급")
def login(payload: LoginRequest) -> TokenResponse:
    """이메일과 비밀번호를 넣으면 토큰 두 개를 준다.

    `access_token` 을 받아 우측 상단 **Authorize** 에 넣으면 로그인이 유지된다.

    **주의 — 지금은 쓸 수 있는 계정이 없다.**
    데이터베이스에 기본으로 들어 있는 계정이 하나 있지만 비밀번호가 제대로 안 들어 있어서
    로그인하면 401 이 난다. 테스트 계정 만드는 방법은 `docs/m1-todo.md` 부록에 있다.
    """
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


@router.post("/refresh", response_model=AccessTokenResponse, summary="접근 토큰 재발급")
def refresh(payload: RefreshRequest) -> AccessTokenResponse:
    """`access_token` 이 만료됐을 때 다시 로그인하는 대신 쓴다.

    `refresh_token` 을 주면 새 `access_token` 을 준다. `refresh_token` 자체는 새로 주지 않는다.
    """
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
