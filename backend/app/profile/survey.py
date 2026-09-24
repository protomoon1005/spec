# 투자 성향 설문 — 문항·배점 정의와 점수 계산. DB 를 모르는 순수 함수다.
#
# ── 문항을 왜 코드에 두나 ────────────────────────────────────────────
# 스키마에 답변을 담는 표(survey_responses)는 있지만 **문항 정의를 담는 표가
# 없다.** 표를 임의로 추가하지 않는다는 규칙이 있어 문항·선택지·배점은 여기
# 상수로 둔다. 답변은 표에 남으므로 "무엇을 답했는지"는 DB 에서 추적된다.
#
# ── 문항 구성 (2026-09-20 결정) ──────────────────────────────────────
# 금융투자협회 표준투자권유준칙의 투자자정보 확인서 구성을 따라 7문항으로 잡았다.
# 허용범위 프리셋의 축이 이미 협회 5단계(안정투자형~공격투자형)라, 판정 근거를
# 같은 계열로 맞추는 편이 설명하기 쉽다.
#
# ⚠ **배점과 가중치는 팀이 정한 프로젝트 기준이고 협회 공시 수치가 아니다.**
#   종목 위험등급과 같은 성격의 값이다 — 실제 공시 배점과 대조해 고칠 수 있도록
#   각 문항에 가중치를 드러내 두고, 판정 결과에 근거를 함께 남긴다.
#
# ── 등급 경계 ────────────────────────────────────────────────────────
# 총점을 0~100 으로 환산하고 20/40/60/80 에서 자른다(협회 권고구간 근사).
# 환산을 쓰는 이유: 나중에 문항을 늘리거나 가중치를 바꿔도 **경계를 다시 잡을
# 필요가 없다.** 최저가 0, 만점이 100 으로 접히기 때문이다.
from __future__ import annotations

from dataclasses import dataclass

SURVEY_VERSION = "v0.1-kofia7"

MIN_SCORE = 1
MAX_SCORE = 5


@dataclass(frozen=True)
class Question:
    code: str
    text: str
    weight: float
    # (선택지 코드, 보기 문구, 점수). 점수가 높을수록 위험을 감수하는 쪽이다.
    choices: tuple[tuple[str, str, int], ...]


# 가중치는 "그 답이 위험 감수 능력을 얼마나 말해 주는가"로 잡았다.
# 감내 가능 손실이 가장 직접적이라 2.0, 투자 기간과 자산 비중이 그다음 1.5,
# 나머지는 1.0 이다.
QUESTIONS: tuple[Question, ...] = (
    Question(
        code="AGE",
        text="연령대가 어떻게 되십니까?",
        weight=1.0,
        choices=(
            ("A1", "만 19세 이상 ~ 40세 미만", 5),
            ("A2", "40세 이상 ~ 50세 미만", 4),
            ("A3", "50세 이상 ~ 60세 미만", 3),
            ("A4", "60세 이상 ~ 70세 미만", 2),
            ("A5", "70세 이상", 1),
        ),
    ),
    Question(
        code="HORIZON",
        text="투자 자금을 얼마 동안 그대로 둘 수 있습니까?",
        weight=1.5,
        choices=(
            ("A1", "6개월 미만", 1),
            ("A2", "6개월 이상 ~ 1년 미만", 2),
            ("A3", "1년 이상 ~ 3년 미만", 3),
            ("A4", "3년 이상 ~ 5년 미만", 4),
            ("A5", "5년 이상", 5),
        ),
    ),
    Question(
        code="EXPERIENCE",
        text="투자해 본 상품 중 가장 위험이 큰 것은 무엇입니까?",
        weight=1.0,
        choices=(
            ("A1", "예금·적금·국채 등 원금이 보장되는 상품", 1),
            ("A2", "채권형 펀드, 원금 부분보장형 상품", 2),
            ("A3", "주식형 펀드, ETF", 3),
            ("A4", "개별 주식 직접 투자", 4),
            ("A5", "선물·옵션, 레버리지·인버스 상품", 5),
        ),
    ),
    Question(
        code="KNOWLEDGE",
        text="투자 상품에 대해 어느 정도 알고 계십니까?",
        weight=1.0,
        choices=(
            ("A1", "투자 경험이 없고 용어도 생소하다", 1),
            ("A2", "예금과 펀드의 차이 정도는 안다", 2),
            ("A3", "상품 설명서를 읽고 대략 이해할 수 있다", 3),
            ("A4", "상품 구조와 위험을 스스로 비교할 수 있다", 4),
            ("A5", "파생상품까지 이해하고 직접 판단할 수 있다", 5),
        ),
    ),
    Question(
        code="INCOME_SOURCE",
        text="현재와 앞으로의 수입을 어떻게 예상하십니까?",
        weight=1.0,
        choices=(
            ("A1", "현재 수입이 없고 앞으로도 기대하기 어렵다", 1),
            ("A2", "현재 수입이 없으나 연금 등 정기 수입이 있다", 2),
            ("A3", "일정한 수입이 있으나 앞으로 줄어들 것 같다", 3),
            ("A4", "일정한 수입이 있고 당분간 유지될 것 같다", 4),
            ("A5", "일정한 수입이 있고 앞으로 늘어날 것 같다", 5),
        ),
    ),
    Question(
        code="ASSET_RATIO",
        text="전체 자산 중 이번에 투자할 금액의 비중은 얼마입니까?",
        weight=1.5,
        choices=(
            ("A1", "10% 이하", 5),
            ("A2", "10% 초과 ~ 20% 이하", 4),
            ("A3", "20% 초과 ~ 30% 이하", 3),
            ("A4", "30% 초과 ~ 40% 이하", 2),
            ("A5", "40% 초과", 1),
        ),
    ),
    Question(
        code="LOSS_TOLERANCE",
        text="투자 원금에 손실이 나면 어디까지 견딜 수 있습니까?",
        weight=2.0,
        choices=(
            ("A1", "원금은 반드시 지켜야 한다", 1),
            ("A2", "10% 미만까지", 2),
            ("A3", "10% 이상 ~ 20% 미만까지", 3),
            ("A4", "20% 이상 ~ 30% 미만까지", 4),
            ("A5", "30% 이상도 감수할 수 있다", 5),
        ),
    ),
)

QUESTIONS_BY_CODE: dict[str, Question] = {q.code: q for q in QUESTIONS}

# 100점 환산 기준 경계. 값 이하이면 그 등급이다. 마지막 등급은 나머지 전부.
LEVEL_CUTOFFS: tuple[tuple[float, int], ...] = ((20.0, 1), (40.0, 2), (60.0, 3), (80.0, 4))

RISK_LEVEL_LABELS: dict[int, str] = {
    1: "안정투자형",
    2: "안정추구형",
    3: "위험중립형",
    4: "성장투자형",
    5: "공격투자형",
}


def score_of(question_code: str, answer_code: str) -> int:
    """한 문항의 답을 점수로 바꾼다. 모르는 코드는 거부한다 — 조용히 0점을
    주면 답을 안 한 것과 잘못 답한 것이 구분되지 않는다."""
    question = QUESTIONS_BY_CODE.get(question_code)
    if question is None:
        raise ValueError(f"없는 문항이다: {question_code}")
    for code, _text, score in question.choices:
        if code == answer_code:
            return score
    raise ValueError(f"{question_code} 에 없는 선택지다: {answer_code}")


def normalized_score(answers: dict[str, str]) -> float:
    """가중 합계를 0~100 으로 환산한다. 7문항이 모두 있어야 한다.

    **최저점을 0 으로 당긴다.** 선택지 점수가 1 부터라 단순히 만점으로 나누면
    최저가 20 이 되고, 그러면 20/40/60/80 경계에서 1등급 구간이 한 점(20.0)밖에
    남지 않는다 — 모든 문항을 최저로 찍어야만 안정투자형이 되는 셈이다.
    실제로 그런 일이 있었다: 연령 70대·기간 6개월 미만·원금보장 희망인 답이
    22.2 로 나와 안정추구형(2)으로 올라갔다.
    최저를 0 으로 당기면 다섯 구간의 폭이 같아진다.
    """
    missing = [q.code for q in QUESTIONS if q.code not in answers]
    if missing:
        raise ValueError(f"답하지 않은 문항이 있다: {', '.join(missing)}")

    total = sum(score_of(q.code, answers[q.code]) * q.weight for q in QUESTIONS)
    weight_sum = sum(q.weight for q in QUESTIONS)
    lowest = MIN_SCORE * weight_sum
    highest = MAX_SCORE * weight_sum
    return round((total - lowest) / (highest - lowest) * 100, 2)


def risk_level_of(normalized: float) -> int:
    for cutoff, level in LEVEL_CUTOFFS:
        if normalized <= cutoff:
            return level
    return 5


@dataclass(frozen=True)
class SurveyResult:
    risk_level: int
    label: str
    normalized_score: float
    survey_version: str
    # 문항별 근거. risk_profiles.provenance 에 그대로 들어간다.
    breakdown: tuple[dict[str, object], ...]


def evaluate(answers: dict[str, str]) -> SurveyResult:
    """답변 묶음 -> 성향 등급과 그 근거."""
    normalized = normalized_score(answers)
    level = risk_level_of(normalized)
    breakdown = tuple(
        {
            "question_code": q.code,
            "answer_code": answers[q.code],
            "score": score_of(q.code, answers[q.code]),
            "weight": q.weight,
        }
        for q in QUESTIONS
    )
    return SurveyResult(
        risk_level=level,
        label=RISK_LEVEL_LABELS[level],
        normalized_score=normalized,
        survey_version=SURVEY_VERSION,
        breakdown=breakdown,
    )


if __name__ == "__main__":
    # 최저/최고/중간이 의도한 등급으로 떨어지는지만 확인한다.
    lowest = {q.code: min(q.choices, key=lambda c: c[2])[0] for q in QUESTIONS}
    highest = {q.code: max(q.choices, key=lambda c: c[2])[0] for q in QUESTIONS}
    middle = {q.code: next(c[0] for c in q.choices if c[2] == 3) for q in QUESTIONS}

    assert evaluate(lowest).risk_level == 1, evaluate(lowest)
    assert evaluate(highest).risk_level == 5, evaluate(highest)
    assert evaluate(middle).risk_level == 3, evaluate(middle)
    assert evaluate(lowest).normalized_score == 0.0
    assert evaluate(highest).normalized_score == 100.0

    # 회귀 방지 — 최저는 아니지만 충분히 보수적인 답이 안정투자형(1)으로 떨어져야 한다.
    # 예전 환산식에서는 22.2 가 나와 2등급으로 올라갔다.
    conservative = {
        "AGE": "A5",
        "HORIZON": "A1",
        "EXPERIENCE": "A1",
        "KNOWLEDGE": "A1",
        "INCOME_SOURCE": "A2",
        "ASSET_RATIO": "A5",
        "LOSS_TOLERANCE": "A1",
    }
    assert evaluate(conservative).risk_level == 1, evaluate(conservative)
    print("survey self-check 통과")
