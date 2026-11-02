"""문장 → 빈칸 채우기(LLM) → 판정(규칙) → 조립(규칙) → 검사 1~5단계(규칙). UI 와 평가는 run() 하나만 부른다.

  run(text)                          첫 입력
  run(text, answers=[{...}])         1번째 되묻기에 답한 뒤
  run(text, answers=[{...}, {...}])  2번째 되묻기에 답한 뒤 (그래도 남으면 UNRESOLVED 거절)

answers 의 각 원소는 {질문 id: 답}. 질문 id 와 선택지는 직전 결과의 questions 에 있다.
LLM 은 원문 문장에만 쓰고(캐시), 답은 규칙이 빈칸에 적용한다 (core.decide).

검사 결과
  막힘   → 거절 VALIDATION (어느 단계에서 막혔는지 같이)
  되묻기 → 되묻기. 되묻기 횟수(최대 2번)는 판정 단계와 같이 센다.
           1~3단계 질문은 규칙으로 답을 적용할 수 없어 '문장을 고쳐서 다시 입력'만 준다.
           4단계 질문은 '무시하고 진행'(빠뜨린 낱말·숫자) / '네'(연산자 확인)로 답하면 그 항목을 뺀다.
  '언제 팔까요?'에 '계속 갖고 있기'로 답했으면 3단계의 '팔 때 조건이 없습니다' 되묻기는 이미 답한 것이라 뺀다.

4단계(근거 검사)에서 걸리면: 걸린 내용을 '검사 메모'로 LLM 에게 알려 주고 한 번만 다시 채운다 (core.slot_fill retry).
  다시 채운 결과로 판정·조립·검사를 처음부터 다시 한다. 그래도 4단계에서 걸리면 되묻는다.
  다시 채운 결과에 처음에 없던 '못 다룸'(unsupported)이 생기면 더 나빠진 것으로 보고 버린다:
  처음 결과를 유지하고 그 4단계 질문으로 되묻는다 (retry["kept_first"] 에 이유).
  첫 시도의 4단계 결과는 PipelineResult.retry 에 남긴다 (평가용).
가격 데이터(prices)를 주면 2단계 NO_DATA 와 5단계 시험 실행을 한다. 화면·평가는 load_prices 를 넘긴다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.assemble import assemble, grounding
from core.decide import MAX_QUESTIONS, MAX_ROUNDS, RESTATE, Decision, Question, Rejection, decide
from core.narrate import Narration, narrate
from core.slot_fill import CACHE_DIR, ChatFn, SlotFillResult, slot_fill
from core.validate import STAGES, PriceSource, StageResult, Stages, ValidationReport, _status, validate

IGNORE_ANSWERS = {"무시하고 진행", "네"}     # 4단계 질문에 이 답이면 그 항목을 뺀다


def load_prices(symbol: str):
    """data/cache 의 일봉 (없으면 FinanceDataReader 로 한 번 받아 캐시)."""
    from core.data import load_ohlcv
    return load_ohlcv(symbol)[0]


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
    retry: Optional[dict] = None                 # 4단계에서 걸려 다시 채웠으면 {"issues": 첫 시도의 4단계 이슈, "fill", ...}

    @property
    def strategy(self):
        return self.report.spec if self.report else None

    @property
    def op_log(self) -> list[dict]:
        return self.decision.op_log if self.decision else []

    def summary(self) -> dict:
        return {
            "verdict": self.verdict,
            "round": self.round,
            "questions": [q.to_dict() for q in self.questions],
            "rejection": self.rejection.to_dict() if self.rejection else None,
            "defaults_applied": (self.spec or {}).get("defaults_applied", []),
            "error": self.error,
        }


def _drop_answered(report: ValidationReport, codes: set[str], qids: set[str] = frozenset()) -> ValidationReport:
    if not codes and not qids:
        return report
    stages = []
    for s in report.stages:
        if s.status == "skipped":
            stages.append(s)
            continue
        kept = [i for i in s.issues if i.code not in codes and i.qid not in qids]
        stages.append(StageResult(s.stage, _status(kept), kept, s.note))
    return ValidationReport(stages=stages, spec=report.spec, trial=report.trial)


def _answered_qids(answers: list[dict]) -> set[str]:
    return {k for d in answers for k, v in d.items() if k.startswith("validate:") and v.strip() in IGNORE_ANSWERS}


def _stage4(report: Optional[ValidationReport]) -> list:
    return [i for i in report.issues if i.stage == 4] if report else []


def unsupported_items(slots) -> set[str]:
    """빈칸 채우기에서 '못 다룸'(unsupported)으로 적힌 곳들의 원문 근거."""
    out: set[str] = set()
    for it in slots.assets.value:
        if it.status == "unsupported":
            out.add(it.evidence or it.said)
    for cs in (slots.entry, slots.exit):
        for c in cs.value.items:
            if c.status == "unsupported":
                out.add(c.evidence)
    for name in ("assets", "entry", "exit", "rebalance", "max_weight", "period", "initial_cash", "costs", "dividend"):
        sl = getattr(slots, name)
        if sl.status == "unsupported" and not getattr(sl.value, "items", None) and not isinstance(sl.value, list):
            out.add(sl.evidence)
    for o in slots.others:
        if o.status == "unsupported":
            out.add(o.evidence)
    return {x.strip() for x in out if x and x.strip()}


def _evaluate(text: str, answers: list[dict], fill: SlotFillResult, stages: Stages,
              prices: Optional[PriceSource]) -> PipelineResult:
    d = decide(fill.slots, answers, text=text)
    if d.verdict == "reject":
        return PipelineResult(text, answers, "reject", fill, d, rejection=d.rejection, round=d.round)
    if d.verdict == "clarify":
        return PipelineResult(text, answers, "clarify", fill, d, questions=d.questions, round=d.round)
    spec = assemble(d.resolved)
    report = validate(spec, stages=stages, grounding=grounding(d.resolved, text, answers), prices=prices)
    report = _drop_answered(report, {"NO_EXIT"} if d.resolved.hold else set(), _answered_qids(answers))
    return PipelineResult(text, answers, "proceed", fill, d, spec, report)


def run(text: str, answers: Optional[list[dict[str, str]]] = None, *, use_cache: bool = True,
        chat_fn: Optional[ChatFn] = None, cache_dir: Path | str = CACHE_DIR, stages: Stages = Stages.ALL,
        prices: Optional[PriceSource] = None) -> PipelineResult:
    text = text.strip()
    answers = list(answers or [])
    fill = slot_fill(text, use_cache=use_cache, chat_fn=chat_fn, cache_dir=cache_dir)
    if fill.slots is None:
        return PipelineResult(text, answers, "error", fill,
                              error=fill.error or "빈칸 채우기 결과를 읽을 수 없습니다.")
    res = _evaluate(text, answers, fill, stages, prices)

    first = _stage4(res.report)
    if first and res.report.failed_stage is None:
        feedback = "\n".join(f"- {i.message}" for i in first)
        fill2 = slot_fill(text, use_cache=use_cache, chat_fn=chat_fn, cache_dir=cache_dir, retry=(fill.raw, feedback))
        retry = {"issues": first, "feedback": feedback, "first_fill": fill, "first_spec": res.spec, "kept_first": None}
        if fill2.slots is None:
            retry["error"] = fill2.error
        elif new := sorted(unsupported_items(fill2.slots) - unsupported_items(fill.slots)):
            # 다시 채운 결과가 더 나빠짐 (새 '못 다룸'). 처음 결과를 유지하고 4단계 되묻기로 간다.
            retry["kept_first"] = f"다시 채우기에서 새 못 다룸: {', '.join(new)}"
            retry["second_fill"] = fill2
        else:
            res = _evaluate(text, answers, fill2, stages, prices)
        res.retry = retry
    if res.verdict != "proceed":
        return res

    report = res.report
    v = report.verdict
    if v == "reject":
        blocked = [i for i in report.issues if i.kind == "block"]
        stage = report.failed_stage
        res.verdict = "reject"
        code = "NO_DATA" if blocked and all(i.code == "NO_DATA" for i in blocked) else "VALIDATION"
        res.rejection = Rejection(code,
                                  f"{stage}단계({STAGES[stage]}) 검사에서 막혔습니다: "
                                  + " / ".join(i.message for i in blocked),
                                  "", ", ".join(dict.fromkeys(i.field for i in blocked)))
        return res
    if v == "clarify":
        qs = [Question(i.qid, i.field, i.question, i.options or [RESTATE], hint=i.message if i.stage == 4 else None)
              for i in report.issues if i.kind == "clarify" and i.question]
        qs = list({q.id: q for q in qs}.values())
        if len(answers) >= MAX_ROUNDS:
            res.verdict = "reject"
            res.rejection = Rejection("UNRESOLVED", f"{MAX_ROUNDS}번 물었는데도 정하지 못한 빈칸이 남았습니다: "
                                      + " / ".join(q.text for q in qs), "", ", ".join(q.slot for q in qs))
            return res
        res.verdict, res.questions, res.round = "clarify", qs[:MAX_QUESTIONS], len(answers) + 1
        return res
    res.narration = narrate(report.spec, dividend_mentioned=res.decision.resolved.dividend_mentioned)
    return res
