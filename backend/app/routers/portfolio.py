"""portfolio 라우터. 전략서 승인(POST /specs/{spec_id}/approve)과 포트폴리오 보기
(GET /portfolios/{portfolio_id}).

승인은 전략서에 대한 결정이라 전략서 아래에 둔다(POST /specs/{spec_id}/validate 와 같은 모양).
결과로 포트폴리오가 하나 생기므로 응답은 포트폴리오다. 흐름과 전제조건은 app/m2/approval.py.
2026-10-20 이전의 501 자리표시 POST /portfolios/{portfolio_id}/approve 는 없앴다 — 승인 전에는
포트폴리오 번호가 없어서 그 주소는 부를 수 없었다.
"""
from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.core.security import AuthUser, require_any_role
from app.m2 import approval
from app.repositories import portfolios

router = APIRouter(prefix="/portfolios", tags=["portfolio"])
spec_router = APIRouter(prefix="/specs", tags=["portfolio"])

_STATUS = {
    approval.NOT_FOUND: status.HTTP_404_NOT_FOUND,
    approval.NOT_DRAFT: status.HTTP_409_CONFLICT,
    approval.NOT_VALIDATED: status.HTTP_409_CONFLICT,
    approval.RUN_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    approval.RUN_OTHER_SPEC: status.HTTP_422_UNPROCESSABLE_ENTITY,
    approval.RUN_NOT_DONE: status.HTTP_409_CONFLICT,
    approval.RUN_STALE: status.HTTP_409_CONFLICT,
}


class ApprovalInfo(BaseModel):
    """APPROVALS 확정 컬럼. 포트폴리오와는 spec_id 로 이어진다."""

    approval_id: int
    run_id: int | None
    user_comment: str | None
    decided_at: datetime | None


class PortfolioResponse(BaseModel):
    """PORTFOLIOS 확정 컬럼 + 승인 기록 (docs/db-erd.md 4.1)."""

    portfolio_id: int
    spec_id: str
    account_no: str | None
    mode: str
    initial_cash: float | None
    status: str | None
    activated_at: datetime | None
    approval: ApprovalInfo | None


class ApproveRequest(BaseModel):
    """승인 근거가 된 백테스트 실행 번호. 이 전략서로, 지금 확정값으로 끝까지 돈 실행이어야 한다."""

    run_id: int
    user_comment: str | None = None


class ApproveResponse(PortfolioResponse):
    # FN-409 초기 관점 가중치(균등 1/3)와 그 기록일(활성화일, KST). 이 날의 판단은
    # 같은 날 기록을 못 읽으므로(strict <) 같은 값의 기본값을 쓰고, 다음 날부터 기록을 읽는다.
    initial_weights: dict[str, float]
    weights_as_of: date


def _response(row: dict) -> dict:
    info = None
    if row.get("approval_id") is not None:
        info = ApprovalInfo(**{key: row[key] for key in ApprovalInfo.model_fields})
    fields = {key: row.get(key) for key in PortfolioResponse.model_fields if key != "approval"}
    return {**fields, "approval": info}


@spec_router.post("/{spec_id}/approve", response_model=ApproveResponse, status_code=status.HTTP_201_CREATED,
                  summary="전략서 승인하고 굴리기 시작")
def approve_spec(
    spec_id: str, payload: ApproveRequest, user: AuthUser = Depends(require_any_role)
) -> ApproveResponse:
    """전략서를 승인하고 가상 운용(포트폴리오)을 시작한다. 실제 돈은 안 쓴다.

    승인한 뒤에는 전략서를 **고칠 수 없다.** 데이터베이스가 막아 놨다.
    바꾸고 싶으면 새로 만들어야 한다 — 승인한 내용과 실제로 굴러간 내용이
    달라지면 나중에 "왜 이렇게 샀는지" 설명할 수 없기 때문이다.

    한 번에 일어나는 일: 승인 기록 → 전략서 승인 상태 → 포트폴리오(가상, 활성) → 관점 가중치
    균등 1/3 기록. 하나라도 실패하면 아무것도 남지 않는다.

    - `run_id` 는 이 전략서로 지금 확정값(범위 · 현금 목표 · 1회 손실 한도 · 최소 간격)으로 끝까지 돈
      백테스트여야 한다. 시작 금액은 그 실행의 금액이다
    - 없거나 남의 전략서·실행이면 404
    - draft 가 아니면(이미 승인했으면) 409 — 두 번 눌러도 포트폴리오는 하나다
    - 마지막 검증이 4단까지 통과하지 않았으면 409
    - 다른 전략서의 실행이면 422, 아직 안 끝났거나 지금 확정값으로 돌지 않은 실행이면 409
    """
    try:
        result = approval.approve(
            spec_id, user_id=user.user_id, run_id=payload.run_id, user_comment=payload.user_comment
        )
    except approval.ApprovalError as exc:
        raise HTTPException(status_code=_STATUS[exc.reason], detail=exc.message) from exc
    row = result.portfolio
    return ApproveResponse(
        **_response(row), initial_weights=result.initial_weights, weights_as_of=row["weights_as_of"]
    )


@router.get("/{portfolio_id}", response_model=PortfolioResponse, summary="굴리는 중인 전략 상태 보기")
def get_portfolio(portfolio_id: int, user: AuthUser = Depends(require_any_role)) -> PortfolioResponse:
    """지금 굴리고 있는 전략 하나의 상태를 본다. 모의투자라 실제 돈은 안 쓴다.

    `status` 가 `active` 면 굴러가는 중이다. 전략서 쪽 상태는 승인 뒤로 `approved` 에서
    바뀌지 않는다(데이터베이스가 승인한 전략서를 얼려 둔다).

    자기 포트폴리오만 볼 수 있다. 없거나 남의 것이면 404.
    """
    row = portfolios.get_portfolio(portfolio_id)
    if row is None or row["user_id"] != user.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="포트폴리오를 찾을 수 없다")
    return PortfolioResponse(**_response(row))
