"""spec 라우터. POST /specs/compile (202 + job_id -> GET /jobs/{job_id}/stream),
GET /specs (내 목록), GET /specs/{spec_id}, DELETE /specs/{spec_id} (draft 만) 가 실제로
동작한다. 나머지는 경로·응답모델·인증만 열어두고 501이다
(docs/infra-spec.md 7단계, 9장).
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.core.errors import raise_not_implemented
from app.core.security import AuthUser, require_any_role
from app.repositories import specs
from app.workers.tasks import compile_spec

router = APIRouter(prefix="/specs", tags=["spec"])


class CompileSpecRequest(BaseModel):
    """새 요청이면 `input_prompt` 만, 되묻기에 답하는 것이면 `session_id` 와 `answer` 를 준다."""

    input_prompt: str | None = None
    session_id: str | None = None
    answer: str | None = None


class CompileSpecAccepted(BaseModel):
    job_id: str


class SpecUniverseItem(BaseModel):
    """SPEC_UNIVERSE 한 줄 + etf_master 의 종목명."""

    ticker: str
    name: str
    weight_min: float | None
    weight_max: float | None
    was_adjusted: bool


class SpecResponse(BaseModel):
    """STRATEGY_SPECS 확정 컬럼만 담는다 (docs/db-erd.md 4.1)."""

    spec_id: str
    user_id: int
    profile_id: int
    hardcap_version: str
    spec_version: str
    name: str
    status: str
    created_at: datetime


class SpecListItem(BaseModel):
    spec_id: str
    name: str
    status: str
    created_at: datetime
    universe_size: int


class SpecDetailResponse(SpecResponse):
    """전략서 보기 — 확정 컬럼에 저장된 내용(요청 문장 · 리밸런싱 · 신호 규칙 · 제약 · 종목)을 더한다."""

    input_prompt: str | None
    rebalance: dict | None
    signal_rules: dict | None
    constraint_user: dict | None
    universe: list[SpecUniverseItem]


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


@router.get("", response_model=list[SpecListItem], summary="내 전략서 목록")
def list_my_specs(user: AuthUser = Depends(require_any_role)) -> list[SpecListItem]:
    """내가 만든 전략서를 최근 것부터 돌려준다. 내용은 `GET /specs/{spec_id}` 로 본다."""
    return [SpecListItem(**row) for row in specs.list_specs(user.user_id)]


@router.post("", response_model=SpecResponse, status_code=status.HTTP_201_CREATED,
             summary="완성된 전략서 바로 등록 · 아직 안 만듦")
def create_spec(user: AuthUser = Depends(require_any_role)) -> SpecResponse:
    """이미 완성된 전략서를 그대로 등록한다. AI 변환을 안 거치는 길이다.

    **아직 안 만들었다 (501).**
    """
    raise_not_implemented()


@router.get("/{spec_id}", response_model=SpecDetailResponse, summary="전략서 보기")
def get_spec(spec_id: str, user: AuthUser = Depends(require_any_role)) -> SpecDetailResponse:
    """전략서 하나를 내용까지 본다. 담은 종목과 종목별 비중 범위(0~1)가 `universe` 에 온다.

    사용자가 승인한 전략서는 **고칠 수 없다.** 데이터베이스가 막아 놨다 —
    승인한 내용과 실제로 굴러간 내용이 달라지면 안 되기 때문이다.

    자기 전략서만 볼 수 있다. 없거나 남의 것이면 404.
    """
    spec = specs.get_spec(spec_id)
    if spec is None or spec["user_id"] != user.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="전략서를 찾을 수 없다")
    return SpecDetailResponse(**spec, universe=specs.get_spec_universe(spec_id))


@router.delete("/{spec_id}", status_code=status.HTTP_204_NO_CONTENT, summary="전략서 지우기 (draft 만)")
def delete_spec(spec_id: str, user: AuthUser = Depends(require_any_role)) -> None:
    """아직 승인하지 않은(draft) 내 전략서를 지운다. 담은 종목과 검증 기록도 함께 지운다.

    - 없거나 남의 것이면 404
    - draft 가 아니면(승인·운용·종료) 409 — 승인한 전략서는 고칠 수도 지울 수도 없다
    - 백테스트·승인·포트폴리오 기록이 붙어 있으면 409
    """
    result = specs.delete_draft_spec(spec_id, user.user_id)
    if result == "not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="전략서를 찾을 수 없다")
    if result == "not_draft":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="draft 전략서만 지울 수 있다")
    if result == "has_records":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="백테스트·승인·포트폴리오 기록이 붙은 전략서는 지울 수 없다",
        )
