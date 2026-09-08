"""spec 라우터. POST /specs/compile 만 실제로 동작한다 (202 + job_id ->
GET /jobs/{job_id}/stream). 나머지는 경로·응답모델·인증만 열어두고 501이다
(docs/infra-spec.md 7단계, 9장).
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from app.core.errors import raise_not_implemented
from app.core.security import AuthUser, require_any_role
from app.workers.tasks import compile_spec

router = APIRouter(prefix="/specs", tags=["spec"])


class CompileSpecRequest(BaseModel):
    input_prompt: str


class CompileSpecAccepted(BaseModel):
    job_id: str


class SpecResponse(BaseModel):
    """STRATEGY_SPECS 확정 컬럼만 담는다 (docs/db-erd.md 4.1). 아직 값을 채우는
    본체가 없으므로 이 모델은 형태만 규정하고 어떤 핸들러도 실제로 반환하지 않는다."""

    spec_id: str
    user_id: int
    profile_id: int
    hardcap_version: str
    spec_version: str
    name: str
    status: str
    created_at: datetime


@router.post("/compile", status_code=status.HTTP_202_ACCEPTED, response_model=CompileSpecAccepted)
def compile_spec_endpoint(
    payload: CompileSpecRequest, user: AuthUser = Depends(require_any_role)
) -> CompileSpecAccepted:
    """202 + job_id -> SSE 구독 패턴의 실 사례. 자연어 -> Spec 컴파일 본체(M1)는
    이번 범위 밖이라, 큐에 올라가는 태스크는 3초 뒤 완료 이벤트만 흘리는 더미다."""
    async_result = compile_spec.delay({"user_id": user.user_id, "input_prompt": payload.input_prompt})
    return CompileSpecAccepted(job_id=async_result.id)


@router.post("", response_model=SpecResponse, status_code=status.HTTP_201_CREATED)
def create_spec(user: AuthUser = Depends(require_any_role)) -> SpecResponse:
    raise_not_implemented()


@router.get("/{spec_id}", response_model=SpecResponse)
def get_spec(spec_id: str, user: AuthUser = Depends(require_any_role)) -> SpecResponse:
    raise_not_implemented()
