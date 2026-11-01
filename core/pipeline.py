"""문장 → 빈칸 채우기(LLM) → 판정(규칙) → 조립(규칙) → 검사 1~3단계(규칙). UI 와 평가는 run() 하나만 부른다.

  run(text)                          첫 입력
  run(text, answers=[{...}])         1번째 되묻기에 답한 뒤
  run(text, answers=[{...}, {...}])  2번째 되묻기에 답한 뒤 (그래도 남으면 UNRESOLVED 거절)

answers 의 각 원소는 {질문 id: 답}. 질문 id 와 선택지는 직전 결과의 questions 에 있다.
LLM 은 원문 문장에만 한 번 쓰고(캐시), 답은 규칙이 빈칸에 적용한다 (core.decide).

검사 결과
  막힘   → 거절 VALIDATION (어느 단계에서 막혔는지 같이)
  되묻기 → 되묻기. 이 질문은 규칙으로 답을 적용할 수 없어 '문장을 고쳐서 다시 입력'만 준다.
           되묻기 횟수(최대 2번)는 판정 단계와 같이 센다.
  '언제 팔까요?'에 '계속 갖고 있기'로 답했으면 3단계의 '팔 때 조건이 없습니다' 되묻기는 이미 답한 것이라 뺀다.
4·5단계(근거·시험 실행)는 아직 없다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.assemble import assemble
from core.decide import MAX_QUESTIONS, MAX_ROUNDS, RESTATE, Decision, Question, Rejection, decide
from core.narrate import Narration, narrate
from core.slot_fill import CACHE_DIR, ChatFn, SlotFillResult, slot_fill
from core.validate import STAGES, StageResult, ValidationReport, _status, validate


@dataclass
class PipelineResult:
    text: str
    answers: list[dict]
    verdict: str                                 # proceed | clarify | reject | error
    fill: SlotFillResult
    decision: Optional[Decision] = None
    spec: Optional[dict] = None                  # 조립한 전략서 (검사 전 dict)
    report: Optional[ValidationReport] = None
    narration: Optional[Narration] = None
    questions: list[Question] = field(default_factory=list)
    rejection: Optional[Rejection] = None
    round: int = 0                               # clarify 면 몇 번째 되묻기인지
    error: Optional[str] = None

    @property
    def strategy(self):
        return self.report.spec if self.report else None

    def summary(self) -> dict:
        return {
            "verdict": self.verdict,
            "round": self.round,
            "questions": [q.to_dict() for q in self.questions],
            "rejection": self.rejection.to_dict() if self.rejection else None,
            "defaults_applied": (self.spec or {}).get("defaults_applied", []),
            "error": self.error,
        }


def _drop_answered(report: ValidationReport, codes: set[str]) -> ValidationReport:
    if not codes:
        return report
    stages = []
    for s in report.stages:
        if s.status in ("skipped", "not_implemented"):
            stages.append(s)
            continue
        kept = [i for i in s.issues if i.code not in codes]
        stages.append(StageResult(s.stage, _status(kept), kept))
    return ValidationReport(stages=stages, spec=report.spec)


def run(text: str, answers: Optional[list[dict[str, str]]] = None, *, use_cache: bool = True,
        chat_fn: Optional[ChatFn] = None, cache_dir: Path | str = CACHE_DIR) -> PipelineResult:
    text = text.strip()
    answers = list(answers or [])
    fill = slot_fill(text, use_cache=use_cache, chat_fn=chat_fn, cache_dir=cache_dir)
    if fill.slots is None:
        return PipelineResult(text, answers, "error", fill,
                              error=fill.error or "빈칸 채우기 결과를 읽을 수 없습니다.")

    d = decide(fill.slots, answers, text=text)
    if d.verdict == "reject":
        return PipelineResult(text, answers, "reject", fill, d, rejection=d.rejection, round=d.round)
    if d.verdict == "clarify":
        return PipelineResult(text, answers, "clarify", fill, d, questions=d.questions, round=d.round)

    spec = assemble(d.resolved)
    report = validate(spec)
    report = _drop_answered(report, {"NO_EXIT"} if d.resolved.hold else set())
    res = PipelineResult(text, answers, "proceed", fill, d, spec, report)
    v = report.verdict
    if v == "reject":
        blocked = [i for i in report.issues if i.kind == "block"]
        stage = report.failed_stage
        res.verdict = "reject"
        res.rejection = Rejection("VALIDATION",
                                  f"{stage}단계({STAGES[stage]}) 검사에서 막혔습니다: "
                                  + " / ".join(i.message for i in blocked),
                                  "", ", ".join(dict.fromkeys(i.field for i in blocked)))
        return res
    if v == "clarify":
        qs = [Question(f"validate:{i.code}", i.field, i.question, [RESTATE])
              for i in report.issues if i.kind == "clarify" and i.question]
        qs = list({q.id: q for q in qs}.values())
        if len(answers) >= MAX_ROUNDS:
            res.verdict = "reject"
            res.rejection = Rejection("UNRESOLVED", f"{MAX_ROUNDS}번 물었는데도 정하지 못한 빈칸이 남았습니다: "
                                      + " / ".join(q.text for q in qs), "", ", ".join(q.slot for q in qs))
            return res
        res.verdict, res.questions, res.round = "clarify", qs[:MAX_QUESTIONS], len(answers) + 1
        return res
    res.narration = narrate(report.spec, dividend_mentioned=d.resolved.dividend_mentioned)
    return res
