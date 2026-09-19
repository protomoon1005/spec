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


@router.post("/survey", status_code=status.HTTP_201_CREATED,
             summary="투자 성향 설문 답 보내기 · 아직 안 만듦")
def submit_survey(
    responses: list[SurveyResponseItem], user: AuthUser = Depends(require_any_role)
) -> None:
    """투자 성향 설문의 답을 문항별로 보낸다. 누가 낸 답인지는 토큰으로 안다.

    **아직 안 만들었다 (501).** 설문 문항과 점수가 정해지면, 여기 보낸 답으로 성향이 계산된다.
    """
    raise_not_implemented()


@router.post("", response_model=RiskProfileResponse, status_code=status.HTTP_201_CREATED,
             summary="투자 성향 정하기 · 아직 안 만듦")
def create_profile(user: AuthUser = Depends(require_any_role)) -> RiskProfileResponse:
    """보낸 설문 답을 점수로 합쳐서 투자 성향을 1~5 로 정한다.
    (1 안정투자형 · 2 안정추구형 · 3 위험중립형 · 4 성장투자형 · 5 공격투자형)

    **성향이 왜 중요하냐면, 어떤 ETF를 최대 몇 %까지 담을 수 있는지가 성향마다 다르기 때문이다.**
    예를 들어 안정투자형은 위험한 ETF의 상한이 0% 라 아예 못 담는다.
    그래서 성향을 모르면 전략서를 만들 수가 없다.

    어떤 기준표로 정했는지도 같이 기록한다. 나중에 기준이 바뀌어도
    예전에 만든 전략이 "그때 무슨 기준이었는지"를 잃지 않게 하려는 것이다.

    **아직 안 만들었다 (501).**
    """
    raise_not_implemented()


@router.get("/me", response_model=RiskProfileResponse, summary="내 투자 성향 보기 · 아직 안 만듦")
def get_my_profile(user: AuthUser = Depends(require_any_role)) -> RiskProfileResponse:
    """내 투자 성향이 몇 등급인지 본다. 누구인지는 토큰으로 안다.

    **아직 안 만들었다 (501).**
    """
    raise_not_implemented()
