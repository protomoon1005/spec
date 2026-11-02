"""전략 관리 라우터. GET /specs (내 전략서 목록), DELETE /specs/{strategy_id} (draft 만).

전략서 만들기 · 보기 · 백테스트는 /v2 (app/routers/strategy_v0_2.py) 에 있다.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.core.security import AuthUser, require_user
from app.repositories import specs

router = APIRouter(prefix="/specs", tags=["spec"])


class SpecListItem(BaseModel):
    strategy_id: str
    name: str
    status: str
    created_at: datetime
    universe_size: int


@router.get("", response_model=list[SpecListItem], summary="내 전략서 목록")
def list_my_specs(user: AuthUser = Depends(require_user)) -> list[SpecListItem]:
    """내가 만든 전략서를 최근 것부터 돌려준다. 내용은 `GET /v2/strategies/{strategy_id}` 로 본다."""
    return [SpecListItem(**row) for row in specs.list_specs(user.user_id)]


@router.delete("/{strategy_id}", status_code=status.HTTP_204_NO_CONTENT, summary="전략서 지우기 (draft 만)")
def delete_spec(strategy_id: str, user: AuthUser = Depends(require_user)) -> None:
    """아직 승인하지 않은(draft) 내 전략서를 지운다. 담은 종목과 검사 기록도 함께 지운다.

    - 없거나 남의 것이면 404
    - draft 가 아니면 409
    - 백테스트 기록이 붙어 있으면 409
    """
    result = specs.delete_draft_spec(strategy_id, user.user_id)
    if result == "not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="전략서를 찾을 수 없다")
    if result == "not_draft":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="draft 전략서만 지울 수 있다")
    if result == "has_records":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="백테스트 기록이 붙은 전략서는 지울 수 없다",
        )
