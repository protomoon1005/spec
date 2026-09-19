"""POST /auth/signup, /auth/login, /auth/refresh — 계정과 JWT 발급.

다른 세 라인이 여기 토큰으로 다른 라우터에 붙으므로 셋 다 501이 아니라 실제로
동작한다.

회원가입은 docs/infra-spec.md 7단계가 명시하지 않아 한동안 비어 있었고,
계정을 만들려면 DB 에 직접 INSERT 해야 했다. 프로토타입 기준으로 정책을 정하고
(이메일 인증 없음 · 권한은 retail 고정 · 비밀번호 8자) 채웠다.
pro/admin 계정은 여전히 DB 나 관리자 경로로만 만든다.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app.core.security import (
    Role,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.repositories.users import get_user_by_email, get_user_by_id, insert_user

router = APIRouter(prefix="/auth", tags=["auth"])

# 가입으로는 retail 만 만들어진다. pro/admin 승격은 DB 나 관리자 경로 몫이다 —
# 요청 본문으로 권한을 고르게 두면 아무나 관리자가 된다.
SIGNUP_ROLE: Role = "retail"


MIN_PASSWORD_LENGTH = 8


class SignupRequest(BaseModel):
    # 이메일 형식은 느슨하게만 본다. 메일을 보내는 경로가 없어서 형식이 정확한지는
    # 중요하지 않고(여기선 그냥 식별자다), 엄격히 보려면 email-validator 패키지가
    # 하나 더 필요하다. 로그인 쪽과 같은 값이 들어오는지만 맞추면 된다.
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH)

    @field_validator("email")
    @classmethod
    def _looks_like_email(cls, value: str) -> str:
        value = value.strip()
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("이메일 형식이 아니다")
        return value


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


@router.post(
    "/signup",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="회원가입",
)
def signup(payload: SignupRequest) -> TokenResponse:
    """이메일과 비밀번호로 계정을 만들고, 바로 쓸 수 있는 토큰까지 돌려준다.

    가입한 계정의 권한은 항상 **일반 사용자(retail)** 다.
    요청으로 권한을 고를 수 없다 — 관리자 계정은 데이터베이스에 직접 넣는다.

    이메일 인증은 하지 않는다(모의투자 졸업작품이라 메일 발송 경로가 없다).
    비밀번호는 최소 8자.

    같은 이메일이 이미 있으면 409.
    """
    user = insert_user(payload.email, hash_password(payload.password), SIGNUP_ROLE)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 가입된 이메일이다",
        )
    return TokenResponse(
        access_token=create_access_token(user.user_id, SIGNUP_ROLE),
        refresh_token=create_refresh_token(user.user_id, SIGNUP_ROLE),
    )


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
