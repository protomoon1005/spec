"""profile 라우터 — 투자 성향 설문과 성향 등급 (M1 1단계).

흐름은 두 단계다.
  1. POST /profile/survey  문항별 답을 보낸다. 여러 번 보내 고칠 수 있다.
  2. POST /profile         쌓인 답 중 최신 것으로 성향을 확정한다.

나누어 둔 이유: 확정 단계에서 성향별 제약 기본값을 읽어 복사하고 판정 근거를
남기는 일이 따로 있어서다. 문항 목록은 GET /profile/survey/questions 로 받는다.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.core.security import AuthUser, require_any_role
from app.profile import survey
from app.repositories import profiles

router = APIRouter(prefix="/profile", tags=["profile"])


class SurveyResponseItem(BaseModel):
    question_code: str
    answer_code: str


class SurveyChoice(BaseModel):
    answer_code: str
    text: str


class SurveyQuestion(BaseModel):
    question_code: str
    text: str
    choices: list[SurveyChoice]


class SurveyForm(BaseModel):
    survey_version: str
    questions: list[SurveyQuestion]


class RiskProfileResponse(BaseModel):
    """RISK_PROFILES 확정 컬럼만 담는다 (docs/db-erd.md 4.1)."""

    profile_id: int
    user_id: int
    risk_level: int
    preset_version: str
    created_at: datetime


@router.get("/survey/questions", response_model=SurveyForm, summary="설문 문항 받기")
def get_survey_questions(user: AuthUser = Depends(require_any_role)) -> SurveyForm:
    """설문에 쓸 문항과 선택지를 내려준다.

    답을 보낼 때 쓰는 `question_code` · `answer_code` 가 여기 들어 있다.
    문항 구성은 금융투자협회 표준투자권유준칙의 투자자정보 확인서를 따라 7문항이다.
    **배점은 팀이 정한 기준이고 협회 공시 수치가 아니다.**
    """
    return SurveyForm(
        survey_version=survey.SURVEY_VERSION,
        questions=[
            SurveyQuestion(
                question_code=question.code,
                text=question.text,
                choices=[
                    SurveyChoice(answer_code=code, text=text) for code, text, _ in question.choices
                ],
            )
            for question in survey.QUESTIONS
        ],
    )


@router.post("/survey", status_code=status.HTTP_201_CREATED, summary="설문 답 보내기")
def submit_survey(
    responses: list[SurveyResponseItem], user: AuthUser = Depends(require_any_role)
) -> None:
    """문항별 답을 보낸다. 누가 낸 답인지는 토큰으로 안다.

    한 번에 전부 보내도 되고 나눠 보내도 된다. 같은 문항에 다시 답하면 새 답이
    쌓이고, 성향을 확정할 때 **가장 최근 답만** 쓴다.

    없는 문항이나 없는 선택지를 보내면 422 다 — 잘못된 답을 조용히 0점으로
    처리하면 "답을 안 했다"와 구분되지 않는다.
    """
    if not responses:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="답이 비었다")

    scored: dict[str, tuple[str, int]] = {}
    for item in responses:
        try:
            score = survey.score_of(item.question_code, item.answer_code)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        scored[item.question_code] = (item.answer_code, score)

    profiles.save_answers(user.user_id, scored)


@router.post(
    "",
    response_model=RiskProfileResponse,
    status_code=status.HTTP_201_CREATED,
    summary="투자 성향 확정",
)
def create_profile(user: AuthUser = Depends(require_any_role)) -> RiskProfileResponse:
    """보낸 답을 점수로 합쳐 투자 성향을 1~5 로 확정한다.
    (1 안정투자형 · 2 안정추구형 · 3 위험중립형 · 4 성장투자형 · 5 공격투자형)

    **성향이 왜 중요하냐면, 어떤 ETF를 최대 몇 %까지 담을 수 있는지가 성향마다
    다르기 때문이다.** 예를 들어 안정투자형은 위험한 ETF의 상한이 0% 라 아예 못
    담는다. 그래서 성향을 모르면 전략서를 만들 수가 없다.

    확정할 때 세 가지를 함께 남긴다.
    - 어떤 기준표 버전으로 정했는지 — 나중에 기준이 바뀌어도 과거 근거가 안 흔들린다
    - 성향별 제약 기본값(현금 하한·최대 낙폭·1회 최대 손실)
    - 문항별 판정 근거 — 어떤 답이 몇 점이어서 이 등급이 나왔는지

    답하지 않은 문항이 있으면 400 이다. 다시 확정하면 **덮어쓰지 않고 새로 쌓는다** —
    이미 만든 전략서가 그때의 성향을 계속 가리켜야 하기 때문이다.
    """
    answers = profiles.get_latest_answers(user.user_id)
    try:
        result = survey.evaluate(answers)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    preset_version = profiles.active_preset_version()
    if preset_version is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="활성 기준표가 없다 — db/seeds/02_preset_v0_1.sql 이 적재됐는지 확인할 것",
        )

    defaults = profiles.get_profile_defaults(preset_version, result.risk_level)
    if defaults is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"기준표 {preset_version} 에 성향 {result.risk_level} 기본값이 없다",
        )

    record = profiles.insert_profile(
        user_id=user.user_id,
        risk_level=result.risk_level,
        preset_version=preset_version,
        defaults=defaults,
        provenance={
            "survey_version": result.survey_version,
            "normalized_score": result.normalized_score,
            "label": result.label,
            "answers": list(result.breakdown),
        },
    )
    return RiskProfileResponse(**record.__dict__)


@router.get("/me", response_model=RiskProfileResponse, summary="내 투자 성향 보기")
def get_my_profile(user: AuthUser = Depends(require_any_role)) -> RiskProfileResponse:
    """내 투자 성향이 몇 등급인지 본다. 누구인지는 토큰으로 안다.

    여러 번 진단했으면 **가장 최근 것**이 나온다. 아직 확정한 적이 없으면 404 다.
    """
    record = profiles.get_latest_profile(user.user_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="확정된 성향이 없다 — 설문을 보내고 POST /profile 로 확정할 것",
        )
    return RiskProfileResponse(**record.__dict__)
