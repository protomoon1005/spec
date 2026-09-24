"""spec 라우터. POST /specs/compile 만 실제로 동작한다 (202 + job_id ->
GET /jobs/{job_id}/stream). 나머지는 경로·응답모델·인증만 열어두고 501이다
(docs/infra-spec.md 7단계, 9장).
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.core.errors import raise_not_implemented
from app.core.security import AuthUser, require_any_role
from app.workers.tasks import compile_spec

router = APIRouter(prefix="/specs", tags=["spec"])


class CompileSpecRequest(BaseModel):
    """새 요청이면 `input_prompt` 만, 되묻기에 답하는 것이면 `session_id` 와 `answer` 를 준다."""

    input_prompt: str | None = None
    session_id: str | None = None
    answer: str | None = None


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
    """사용자가 쓴 문장("배당 잘 나오는 걸로 안전하게")을 받아 전략서를 만든다.

    오래 걸리는 일이라 결과를 기다리지 않고 **작업 번호(`job_id`)를 먼저 준다.**
    그 번호로 `GET /jobs/{job_id}/stream` 을 열어 두면 진행 상황이 실시간으로 온다.
    **한 번에 2분이 넘게 걸린다.**

    끝나면 스트림에 결과가 실려 온다. 셋 중 하나다.

    - `completed` — 전략서를 만들어 저장했다. `spec_id` 가 함께 온다
    - `need_answer` — 정보가 모자라 되묻는다. `session_id` · `question` · `choices` 가 온다.
      답을 정해 **이 주소를 다시 부르되 `session_id` 와 `answer` 를 실어 보낸다**
      (`input_prompt` 는 비운다)
    - `failed` — 만들 수 없다. `reason` 에 사용자에게 보여 줄 이유가 담긴다

    먼저 설문을 마쳐 성향을 확정해야 한다. 성향을 모르면 종목별 허용 범위가 정해지지 않는다.
    """
    if payload.session_id:
        if not payload.answer:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="session_id 를 줬으면 answer 도 줘야 한다",
            )
        job = {"user_id": user.user_id, "session_id": payload.session_id, "answer": payload.answer}
    else:
        if not payload.input_prompt:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="input_prompt 가 필요하다",
            )
        job = {"user_id": user.user_id, "input_prompt": payload.input_prompt}

    async_result = compile_spec.delay(job)
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
