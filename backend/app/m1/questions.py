# 되묻기 질문 (M1 6단계).
#
# 설명을 주석에 두는 이유는 같은 폴더의 다른 파일과 같다 — as_of 가드 사정권이다.
#
# ── 질문은 LLM 이 만들지 않는다 ──────────────────────────────────────
# 되물어야 하는 상황은 세 가지뿐이고 전부 **우리가 아는 사실**에서 나온다.
# 무엇이 모호한지, 어떤 후보가 있는지는 이미 손에 있다. 그걸 LLM 에게 다시 설명해
# 질문을 받아 오면 느려지고(한 번에 수십 초다), 매번 문구가 달라지고, 있지도 않은
# 선택지를 지어낼 수 있다.
#
# 대신 **실제 후보를 그대로 보여 준다.** "어느 것인가요?" 하고 목록을 주면 사용자가
# 고르기만 하면 된다. 이게 자유 문장으로 되묻는 것보다 답하기 쉽다.
from __future__ import annotations

from dataclasses import dataclass

from app.m1.candidates import CandidateSet
from app.m1.intent import ResolvedIntent
from app.repositories import etf_master

KIND_AMBIGUOUS_NAME = "ambiguous_name"
KIND_NO_DIRECTION = "no_direction"
KIND_NOTHING_HOLDABLE = "nothing_holdable"

MAX_CHOICES = 6


@dataclass(frozen=True)
class Question:
    kind: str
    text: str
    choices: tuple[str, ...] = ()


def for_intent(resolved: ResolvedIntent) -> Question | None:
    # 이름이 모호하면 그것부터 푼다. 종목이 정해지면 나머지가 따라온다.
    if resolved.unresolved_names:
        mention = resolved.unresolved_names[0]
        matches = etf_master.find_by_name(mention, limit=MAX_CHOICES)
        if matches:
            choices = tuple(f"{r.ticker} {r.name}" for r in matches)
            return Question(
                kind=KIND_AMBIGUOUS_NAME,
                text=f"'{mention}' 으로 찾으면 여러 개가 나옵니다. 어느 것을 말씀하신 건가요?",
                choices=choices,
            )
        return Question(
            kind=KIND_AMBIGUOUS_NAME,
            text=f"'{mention}' 이라는 종목을 찾지 못했습니다. 정확한 이름이나 종목코드를 알려주세요.",
        )

    intent = resolved.intent
    if not (intent.keywords or intent.sectors or intent.asset_groups or resolved.requested_tickers):
        return Question(
            kind=KIND_NO_DIRECTION,
            text="어떤 쪽으로 굴리고 싶으신지 한 가지만 알려주세요.",
            choices=("안전하게", "배당을 받고 싶다", "특정 업종에 집중", "종목을 직접 고르겠다"),
        )
    return None


def for_empty_candidates(candidate_set: CandidateSet) -> Question:
    # 거절 사유가 곧 답이다. 왜 못 담는지 알려 주지 않으면 사용자가 같은 요청을 반복한다.
    reasons = [f"{r.name or r.ticker} — {r.reason}" for r in candidate_set.rejected]
    head = "요청하신 조건으로 담을 수 있는 종목이 없습니다."
    if reasons:
        head += " 말씀하신 종목은 이런 이유로 담을 수 없습니다."
    return Question(
        kind=KIND_NOTHING_HOLDABLE,
        text=head + " 다른 조건을 알려주세요.",
        choices=tuple(reasons),
    )
