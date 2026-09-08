"""profile 라우터. 경로·응답모델·인증만 열어두고 본체는 501이다
(docs/infra-spec.md 7단계, 9장)."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from app.core.errors import raise_not_implemented
from app.core.security import AuthUser, require_any_role

router = APIRouter(prefix="/profile", tags=["profile"])


class SurveyResponseItem(BaseModel):
    question_code: str
    answer_code: str


class RiskProfileResponse(BaseModel):
    """RISK_PROFILES 확정 컬럼만 담는다 (docs/db-erd.md 4.1)."""

    profile_id: int
    user_id: int
    risk_level: int
    preset_version: str
    created_at: datetime


@router.post("/survey", status_code=status.HTTP_201_CREATED)
def submit_survey(
    responses: list[SurveyResponseItem], user: AuthUser = Depends(require_any_role)
) -> None:
    raise_not_implemented()


@router.post("", response_model=RiskProfileResponse, status_code=status.HTTP_201_CREATED)
def create_profile(user: AuthUser = Depends(require_any_role)) -> RiskProfileResponse:
    raise_not_implemented()


@router.get("/me", response_model=RiskProfileResponse)
def get_my_profile(user: AuthUser = Depends(require_any_role)) -> RiskProfileResponse:
    raise_not_implemented()
