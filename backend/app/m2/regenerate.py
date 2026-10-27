# FN-306 재생성 피드백. LLM 을 부르지 않는다 — "다시 만들어야 한다" 와 그 사유만 구조화한다.
#
# 재생성 사유는 1단(스키마)·2단(참조) 실패뿐이다. 3단 논리 위반은 사용자가 요청을
# 고쳐야 하는 문제라 다시 뽑아도 같고, 범위 실행가능성은 FN-304 가 자동 보정한다
# (docs/m2-algorithms.md 1장 · 6장).
from __future__ import annotations

from collections.abc import Sequence

from app.m2.stages import FAILED, Regeneration, StageResult

REGENERATE_STAGES = (1, 2)


def regeneration_for(stages: Sequence[StageResult]) -> Regeneration:
    reasons = tuple(
        {"stage": s.stage, **v.as_dict()}
        for s in stages
        if s.stage in REGENERATE_STAGES and s.status == FAILED
        for v in s.violations
    )
    return Regeneration(required=bool(reasons), reasons=reasons)
