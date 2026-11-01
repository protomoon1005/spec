"""POST /auth/signup, /auth/login, /auth/refresh — 계정과 JWT 발급.

다른 세 라인이 여기 토큰으로 다른 라우터에 붙으므로 셋 다 501이 아니라 실제로
동작한다.

프로토타입이라 보안은 보지 않는다. 입력은 아무 문자열인 username 하나고,
중복 가입만 막는다(409). 비밀번호는 없다 — 이름만 알면 그 계정으로 로그인된다.
DB 컬럼은 여전히 users.email 이고 password_hash 에는 빈 문자열을 넣는다.
pro/admin 계정은 여전히 DB 나 관리자 경로로만 만든다.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app.core.security import (
    AuthUser,
    Role,
    create_access_token,
    create_refresh_token,
    decode_token,
    require_any_role,
)
from app.repositories.users import get_user_by_email, get_user_by_id, insert_user

router = APIRouter(prefix="/auth", tags=["auth"])

# 가입으로는 retail 만 만들어진다. pro/admin 승격은 DB 나 관리자 경로 몫이다 —
# 요청 본문으로 권한을 고르게 두면 아무나 관리자가 된다.
SIGNUP_ROLE: Role = "retail"


class UsernameRequest(BaseModel):
    # 형식 제한 없음. 앞뒤 공백만 뗀다 — 가입과 로그인이 같은 값을 보게.
    username: str = Field(min_length=1, max_length=254)

    @field_validator("username")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("username 이 비어 있다")
        return value


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
def signup(payload: UsernameRequest) -> TokenResponse:
    """username 하나로 계정을 만들고, 바로 쓸 수 있는 토큰까지 돌려준다.

    형식 제한 없음. 가입한 계정의 권한은 항상 **일반 사용자(retail)** 다.
    같은 username 이 이미 있으면 409.
    """
    user = insert_user(payload.username, "", SIGNUP_ROLE)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 있는 username 이다",
        )
    return TokenResponse(
        access_token=create_access_token(user.user_id, SIGNUP_ROLE),
        refresh_token=create_refresh_token(user.user_id, SIGNUP_ROLE),
    )


@router.post("/login", response_model=TokenResponse, summary="로그인 — 토큰 발급")
def login(payload: UsernameRequest) -> TokenResponse:
    """username 을 넣으면 토큰 두 개를 준다.

    `access_token` 을 받아 우측 상단 **Authorize** 에 넣으면 로그인이 유지된다.
    계정이 없으면 `POST /auth/signup` 으로 먼저 만든다.
    """
    user = get_user_by_email(payload.username)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="없는 username 이다",
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


class MeResponse(BaseModel):
    user_id: int
    username: str
    role: str


@router.get("/me", response_model=MeResponse, summary="내 계정 보기")
def me(user: AuthUser = Depends(require_any_role)) -> MeResponse:
    """토큰 주인이 누구인지 돌려준다.

    로그인 화면이 입력한 토큰이 입력한 아이디의 것인지 비교하는 데 쓴다
    (docs/frontend_milestone.md 2단계).
    """
    record = get_user_by_id(user.user_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="사용자를 찾을 수 없다")
    return MeResponse(user_id=record.user_id, username=record.email, role=record.role)
