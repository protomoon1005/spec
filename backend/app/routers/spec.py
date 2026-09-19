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


@router.post("/compile", status_code=status.HTTP_202_ACCEPTED, response_model=CompileSpecAccepted,
             summary="요청 문장으로 전략서 만들기")
def compile_spec_endpoint(
    payload: CompileSpecRequest, user: AuthUser = Depends(require_any_role)
) -> CompileSpecAccepted:
    """사용자가 쓴 문장("배당 잘 나오는 걸로 안전하게")을 받아 전략서 만들기를 시작한다.

    오래 걸리는 일이라 결과를 기다리지 않고 **작업 번호(`job_id`)를 먼저 준다.**
    그 번호로 `GET /jobs/{job_id}/stream` 을 열어 두면 진행 상황이 실시간으로 온다.

    **요청 접수까지는 실제로 된다.** 다만 지금 돌아가는 작업은 3초 기다렸다가
    "끝났다"고만 하는 껍데기다 — 진짜 변환은 만드는 중이다.
    """
    async_result = compile_spec.delay({"user_id": user.user_id, "input_prompt": payload.input_prompt})
    return CompileSpecAccepted(job_id=async_result.id)


@router.post("", response_model=SpecResponse, status_code=status.HTTP_201_CREATED,
             summary="완성된 전략서 바로 등록 · 아직 안 만듦")
def create_spec(user: AuthUser = Depends(require_any_role)) -> SpecResponse:
    """이미 완성된 전략서를 그대로 등록한다. AI 변환을 안 거치는 길이다.

    **아직 안 만들었다 (501).**
    """
    raise_not_implemented()


@router.get("/{spec_id}", response_model=SpecResponse, summary="전략서 보기 · 아직 안 만듦")
def get_spec(spec_id: str, user: AuthUser = Depends(require_any_role)) -> SpecResponse:
    """전략서 하나를 본다.

    사용자가 승인한 전략서는 **고칠 수 없다.** 데이터베이스가 막아 놨다 —
    승인한 내용과 실제로 굴러간 내용이 달라지면 안 되기 때문이다.

    **아직 안 만들었다 (501).**
    """
    raise_not_implemented()
