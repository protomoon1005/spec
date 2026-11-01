"""4단계 검증기 (docs/demo-spec.md §B5). AI 미사용.

  1. 형식 — Pydantic 스키마 통과 (clarify 면 질문 ≥1 · 모두 물음표로 끝남, reject 면 사유 존재)
  2. 참조 — 지표·연산자·종목이 사전에 존재, 출처 매핑 경로가 실제 필드를 가리킴
            + 탈출구(UNKNOWN·UNSUPPORTED·unmapped_phrases) 감지
            + 원문 대조: 명세의 종목명·별칭과 출처 구절이 원문(+clarify 답변)에 실제로 있는지
  3. 논리 — 실행 가능한 판정, 매수 조건 ≥1, 매도 수단 ≥1, 비중 합 ≤ 1, 시작 < 종료,
            단위가 다른 지표 비교·자기 자신 비교·자명한 모순 금지 (간단 규칙만)
  4. 범위 — 손절·익절 0~0.5, 보유기간 1~250, 종목 ≤ 3, 비중 0~1 등

앞 단계가 실패하면 뒤 단계는 실행하지 않는다(skipped). 한 단계 안의 문제는 모두 모은다.

문제(Issue)마다 kind 가 있다 — 판정 원칙(docs/demo-spec.md §B4): "질문해서 해결되면 clarify,
뭐라고 답해도 안 되면 reject".
  error   : 명세 자체가 잘못됨 (LLM 오류) → 최종 판정 invalid
  clarify : 사용자에게 물으면 해결됨 (종목 없음, 매도 조건 없음, 사전에 없는 표현 등) → question 을 붙인다
  reject  : 뭐라고 답해도 실행 불가 (재무지표, 분봉 등) → reject_code 를 붙인다
최종 판정(ValidationReport.verdict)은 LLM 판정을 더 안전한 쪽으로만 바꾼다 (ok → clarify → reject).
LLM 이 ok 라고 해도 탈출구 값이 있으면 백테스트로 가지 않는다.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import BaseModel, ValidationError

from core.dictionary import INDICATORS, OPERATORS, load_expressions, load_tickers
from core.schema import UNKNOWN_TICKER, UNSUPPORTED_INDICATOR, Condition, DemoSpec, RefMul, RejectReason

STAGES = {1: "형식", 2: "참조", 3: "논리", 4: "범위"}

LIMITS = {
    "stop_loss": (0.0, 0.5),       # (초과, 이하)
    "take_profit": (0.0, 0.5),
    "max_hold_days": (1, 250),     # 이상, 이하
    "max_universe": 3,
}
_EPS = 1e-9


@dataclass
class Issue:
    stage: int
    field: str
    message: str
    kind: str = "error"                  # "error" | "clarify" | "reject"
    question: Optional[str] = None       # kind=clarify 일 때 사용자에게 물을 질문
    reject_code: Optional[str] = None    # kind=reject 일 때 사유 코드

    @property
    def stage_name(self) -> str:
        return STAGES[self.stage]

    def __str__(self) -> str:
        return f"[{self.stage}단계 {self.stage_name}] {self.field}: {self.message}"


@dataclass
class StageResult:
    stage: int
    status: str                     # "passed" | "failed" | "skipped"
    issues: list[Issue] = field(default_factory=list)

    @property
    def name(self) -> str:
        return STAGES[self.stage]


@dataclass
class ValidationReport:
    stages: list[StageResult]
    spec: Optional[DemoSpec]

    @property
    def verdict(self) -> str:
        """최종 판정: ok | clarify | reject | invalid. LLM 판정보다 안전한 쪽으로만 바뀐다."""
        issues = self.issues
        if self.spec is None or any(i.kind == "error" for i in issues):
            return "invalid"
        if self.spec.verdict == "reject" or any(i.kind == "reject" for i in issues):
            return "reject"
        if self.spec.verdict == "clarify" or any(i.kind == "clarify" for i in issues):
            return "clarify"
        return "ok"

    @property
    def ok(self) -> bool:
        return self.verdict == "ok"

    @property
    def overridden(self) -> bool:
        """검증기가 LLM 판정을 바꿨는가 (예: LLM 은 ok, 검증기는 clarify)."""
        return self.spec is not None and self.verdict in ("clarify", "reject") and self.verdict != self.spec.verdict

    @property
    def questions(self) -> list[str]:
        """최종 판정이 clarify 일 때 사용자에게 물을 질문 (LLM 질문 + 검증기 질문, 중복 제거, 최대 3개)."""
        if self.verdict != "clarify":
            return []
        qs = list(self.spec.questions) if self.spec.verdict == "clarify" else []
        qs += [i.question for i in self.issues if i.kind == "clarify" and i.question]
        return list(dict.fromkeys(q.strip() for q in qs))[:3]

    @property
    def reject_reason(self) -> Optional[RejectReason]:
        if self.verdict != "reject":
            return None
        if self.spec.verdict == "reject" and self.spec.reject_reason is not None:
            return self.spec.reject_reason
        i = next(i for i in self.issues if i.kind == "reject")
        return RejectReason(code=i.reject_code or "UNSUPPORTED_CONCEPT", detail=i.message)

    @property
    def issues(self) -> list[Issue]:
        return [i for s in self.stages for i in s.issues]

    @property
    def failed_stage(self) -> Optional[int]:
        return next((s.stage for s in self.stages if s.status == "failed"), None)


# ---------------------------------------------------------------- 1. 형식

_UNION_TAGS = {"float", "int", "str", "none", "bool"}


def _loc_to_path(loc: tuple) -> str:
    """Pydantic loc → 'entry.conditions[0].right'. Union 분기 태그('float', 'RefMul', "literal[...]")에서 멈춘다."""
    out = ""
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        elif re.fullmatch(r"[a-z_]+", part) and part not in _UNION_TAGS:
            out += f".{part}" if out else part
        else:
            break
    return out or "(최상위)"


def _format_error(err: dict) -> str:
    t, inp = err["type"], err.get("input")
    if t == "missing":
        return "필수 항목이 없습니다."
    if t == "extra_forbidden":
        return "스키마에 없는 항목입니다."
    if t == "literal_error":
        allowed = err.get("ctx", {}).get("expected", "")
        return f"허용되지 않은 값입니다: {inp!r}. 허용값: {allowed}"
    if t in ("float_parsing", "float_type", "int_parsing", "int_type", "int_from_float"):
        return f"숫자여야 합니다: {inp!r}"
    if t in ("string_type",):
        return f"문자열이어야 합니다: {inp!r}"
    if t in ("list_type",):
        return "목록이어야 합니다."
    if t in ("model_type", "dict_type", "model_attributes_type"):
        return "객체여야 합니다."
    if t == "too_long":
        return f"항목이 너무 많습니다 (최대 {err.get('ctx', {}).get('max_length')}개)."
    if t == "string_pattern_mismatch":
        return f"YYYY-MM-DD 형식이어야 합니다: {inp!r}"
    if t in ("clarify_questions", "reject_reason"):
        return err["msg"]
    if t == "value_error":
        return f"올바른 날짜가 아닙니다: {inp!r}"
    return f"형식 오류 ({t}): {err.get('msg')}"


def _stage_format(data: Any) -> tuple[Optional[DemoSpec], list[Issue]]:
    if isinstance(data, DemoSpec):
        return data, []
    if isinstance(data, (str, bytes)):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as e:
            return None, [Issue(1, "(최상위)", f"JSON 으로 읽을 수 없습니다: {e.msg} (위치 {e.pos})")]
    try:
        return DemoSpec.model_validate(data), []
    except ValidationError as e:
        issues: dict[str, Issue] = {}
        for err in e.errors():
            path = _loc_to_path(err["loc"])
            loc = err["loc"]
            if "right" in loc and loc.index("right") < len(loc) - 1:   # Union 분기 오류 → 하나로 묶음
                msg = "지표 id, 숫자, 또는 {\"ref\": 지표 id, \"mul\": 배수} 중 하나여야 합니다."
            else:
                msg = _format_error(err)
            issues.setdefault(path, Issue(1, path, msg))
        return None, list(issues.values())


# ---------------------------------------------------------------- 2. 참조

def _iter_conditions(spec: DemoSpec):
    for side in ("entry", "exit"):
        rule = getattr(spec, side)
        if rule is None:
            continue
        for i, c in enumerate(rule.conditions):
            yield f"{side}.conditions[{i}]", c


def _resolve(spec: DemoSpec, path: str) -> bool:
    """'entry.conditions[0]', 'exit.stop_loss', 'universe[1].weight', 'backtest' 같은 경로가 존재하는지."""
    if not re.fullmatch(r"[a-z_]+(\[\d+\])?(\.[a-z_]+(\[\d+\])?)*", path):
        return False
    cur: Any = spec
    for name, idx in re.findall(r"([a-z_]+)(?:\[(\d+)\])?", path):
        if not isinstance(cur, BaseModel) or name not in type(cur).model_fields:
            return False
        cur = getattr(cur, name)
        if idx:
            if not isinstance(cur, list) or int(idx) >= len(cur):
                return False
            cur = cur[int(idx)]
    return True


_NORM_RE = re.compile(r"[\s.,!?？~'\"“”‘’·…()\[\]{}]")
SOURCE_MARKER = "[추가 질문에 대한 사용자 답변]"


def _norm(s: str) -> str:
    """원문 대조용 정규화: 공백·문장부호 제거, 소문자. 'KODEX 200' ~ 'kodex200'."""
    return _NORM_RE.sub("", s).lower()


def source_text_of(input_text: str) -> str:
    """명세화 입력에서 '사용자가 실제로 쓴 글'만 남긴다 = 원문 + clarify 답변.
    core.specify.compose_refined_input 이 붙인 질문 문장은 뺀다 (질문의 예시 종목명이
    원문 대조를 통과시키면 안 되므로). 여러 번 refine 해도 같은 형식이라 재귀적으로 동작한다."""
    if SOURCE_MARKER not in input_text:
        return input_text
    head, _, tail = input_text.partition(SOURCE_MARKER)
    answers = [ln.strip()[len("답변:"):].strip() for ln in tail.splitlines() if ln.strip().startswith("답변:")]
    return "\n".join([source_text_of(head).strip(), *answers])


def _phrase_entry(pid: str) -> dict:
    return next(p for p in load_expressions()["phrases"] if p["id"] == pid)


def _question(pid: str, **kw) -> str:
    return _phrase_entry(pid)["question"].format(**kw)


def _classify_unmapped(phrase: str, idx: int) -> Issue:
    """사전에 매핑 못 한 구절 → 표현 사전(phrases)의 clarify/reject 항목과 대조해 종류를 정한다.
    reject 항목(재무지표·분봉·미래 참조 등)에 걸리면 reject, 그 외는 clarify."""
    path = f"unmapped_phrases[{idx}]"
    entries = [p for p in load_expressions()["phrases"] if p["verdict"] in ("clarify", "reject")]
    for e in sorted(entries, key=lambda e: e["verdict"] != "reject"):      # reject 우선
        hit = next((pat for pat in e["patterns"] if pat and _norm(pat) in _norm(phrase)), None)
        if hit is None:
            continue
        if e["verdict"] == "reject":
            return Issue(2, path, f"'{phrase}': {e['detail']}", kind="reject", reject_code=e["reject_code"])
        return Issue(2, path, f"사전에 매핑할 수 없는 구절입니다: '{phrase}'", kind="clarify", question=e["question"])
    return Issue(2, path, f"사전에 매핑할 수 없는 구절입니다: '{phrase}'", kind="clarify",
                 question=_question("unmapped_generic", phrase=phrase))


def _stage_reference(spec: DemoSpec, source_text: Optional[str] = None) -> list[Issue]:
    issues: list[Issue] = []
    tickers = load_tickers()
    if source_text is None and spec.provenance is not None and spec.provenance.input_text:
        source_text = spec.provenance.input_text
    src = _norm(source_text_of(source_text)) if source_text else None   # None 이면 원문 대조 생략 (수기 명세)

    # 탈출구: 사전에 없는 구절
    for i, phrase in enumerate(spec.unmapped_phrases):
        if phrase.strip():
            issues.append(_classify_unmapped(phrase.strip(), i))

    for i, u in enumerate(spec.universe):
        if u.ticker == UNKNOWN_TICKER:
            issues.append(Issue(2, f"universe[{i}].ticker", "종목 사전에 없는 종목입니다 (UNKNOWN).",
                                kind="clarify", question=_question("no_universe")))
            continue
        info = tickers.get(u.ticker)
        if info is None:
            issues.append(Issue(2, f"universe[{i}].ticker",
                                f"종목 사전(data/tickers.json)에 없는 종목코드입니다: {u.ticker}"))
        elif u.name != info.name:
            issues.append(Issue(2, f"universe[{i}].name",
                                f"종목코드 {u.ticker} 의 사전 이름 '{info.name}' 와 다릅니다: '{u.name}'"))
        elif src is not None and not any(_norm(a) in src for a in (info.name, *info.aliases)):
            # 원문 대조: 문장에 없는 종목을 넣었다 (기본값 삽입·다른 종목으로 바꿔치기)
            issues.append(Issue(2, f"universe[{i}]",
                                f"원문에 '{info.name}'(별칭: {', '.join(info.aliases)})이 나오지 않습니다. "
                                f"문장에 없는 종목은 넣을 수 없습니다.",
                                kind="clarify", question=_question("no_universe")))
    for path, c in _iter_conditions(spec):
        refs = [("left", c.left), ("right", c.right.ref if isinstance(c.right, RefMul) else c.right)]
        if any(v == UNSUPPORTED_INDICATOR for _, v in refs):
            for side, v in refs:
                if v == UNSUPPORTED_INDICATOR:
                    # 구절이 unmapped_phrases 에 있으면 그쪽 질문을 쓴다. 없으면 일반 질문.
                    q = None if spec.unmapped_phrases else _question("unmapped_generic", phrase="사전에 없는 지표")
                    issues.append(Issue(2, f"{path}.{side}", "지표 사전에 없는 지표입니다 (UNSUPPORTED).",
                                        kind="clarify", question=q))
            continue
        if c.left not in INDICATORS:
            issues.append(Issue(2, f"{path}.left", f"지표 사전에 없는 지표입니다: {c.left}"))
        if c.op not in OPERATORS:
            issues.append(Issue(2, f"{path}.op", f"연산자 사전에 없는 연산자입니다: {c.op}"))
        r = c.right
        ref = r.ref if isinstance(r, RefMul) else (r if isinstance(r, str) else None)
        if ref is not None and ref not in INDICATORS:
            issues.append(Issue(2, f"{path}.right", f"지표 사전에 없는 지표입니다: {ref}"))
        if isinstance(r, RefMul) and c.left != "VOLUME":
            issues.append(Issue(2, f"{path}.right",
                                "배수 표현 {ref, mul} 은 거래량(VOLUME) 비교에만 쓸 수 있습니다."))
    if spec.provenance is not None:
        for i, m in enumerate(spec.provenance.mappings):
            if not _resolve(spec, m.field):
                issues.append(Issue(2, f"provenance.mappings[{i}].field",
                                    f"명세에 없는 필드를 가리킵니다: {m.field}"))
            if src is not None and (not _norm(m.phrase) or _norm(m.phrase) not in src):
                issues.append(Issue(2, f"provenance.mappings[{i}].phrase",
                                    f"출처 구절이 원문에 없습니다: '{m.phrase}'"))
        for i, d in enumerate(spec.provenance.defaults_applied):
            if not _resolve(spec, d):
                issues.append(Issue(2, f"provenance.defaults_applied[{i}]",
                                    f"명세에 없는 필드를 가리킵니다: {d}"))
    return issues


# ---------------------------------------------------------------- 3. 논리

def _family(x) -> Optional[str]:
    if isinstance(x, RefMul):
        return INDICATORS[x.ref]["family"]
    if isinstance(x, str):
        return INDICATORS[x]["family"]
    return None  # 숫자


_STATE_OF = {"lt": "lt", "gt": "gt", "cross_below": "lt", "cross_above": "gt"}  # 교차한 날엔 그 상태도 참


def _bounds(c: Condition) -> Optional[tuple[str, str, float]]:
    """숫자 상태 조건이면 (지표, 'lt'|'gt', 값). 아니면 None."""
    if c.op in ("lt", "gt") and isinstance(c.right, (int, float)):
        return c.left, c.op, float(c.right)
    return None


def _implies(a: Condition, b: Condition) -> bool:
    """a 가 참인 날 b 도 반드시 참인가 (간단 규칙만).
    - 동일 조건
    - b 가 상태(lt/gt)이고, a 가 같은 방향의 상태·교차이며 같은 지표끼리 비교
      (숫자면 a 의 기준이 더 엄격할 때: 'RSI 30 아래로 내려감' ⇒ 'RSI 50 아래')"""
    if a == b:
        return True
    if b.op not in ("lt", "gt") or _STATE_OF[a.op] != b.op or a.left != b.left:
        return False
    if isinstance(a.right, (int, float)) and isinstance(b.right, (int, float)):
        return a.right <= b.right if b.op == "lt" else a.right >= b.right
    return a.right == b.right


def _contradicts(a: Condition, b: Condition) -> bool:
    """a 와 b 가 같은 날 동시에 참일 수 없는가 (간단 규칙만)."""
    ba, bb = _bounds(a), _bounds(b)
    if ba and bb and ba[0] == bb[0] and ba[1] != bb[1]:
        lo = ba[2] if ba[1] == "gt" else bb[2]
        hi = ba[2] if ba[1] == "lt" else bb[2]
        return lo >= hi       # x > lo 이고 x < hi 인 x 가 없음
    if a.left == b.left and a.right == b.right and {a.op, b.op} in ({"lt", "gt"}, {"cross_above", "cross_below"}):
        return True
    return False


def _stage_logic(spec: DemoSpec) -> list[Issue]:
    issues: list[Issue] = []
    if spec.verdict != "ok":
        # 질문 형식·사유 존재는 1단계(형식)에서 이미 확인했다.
        label = {"clarify": "추가 질문 필요(clarify)", "reject": "실행 불가(reject)"}[spec.verdict]
        issues.append(Issue(3, "verdict", f"판정이 {label}입니다. ok 판정만 백테스트할 수 있습니다.",
                            kind=spec.verdict))
        return issues

    if spec.questions:
        issues.append(Issue(3, "questions", "판정이 ok 인데 추가 질문이 남아 있습니다."))
    if spec.reject_reason is not None:
        issues.append(Issue(3, "reject_reason", "판정이 ok 인데 실행 불가 사유가 있습니다."))

    if not spec.universe:
        issues.append(Issue(3, "universe", "대상 종목이 없습니다.", kind="clarify", question=_question("no_universe")))
    seen: dict[str, int] = {}
    for i, u in enumerate(spec.universe):
        if u.ticker in seen:
            issues.append(Issue(3, f"universe[{i}].ticker", f"종목 {u.ticker} 가 universe[{seen[u.ticker]}] 와 중복됩니다."))
        seen.setdefault(u.ticker, i)
    total = sum(u.weight for u in spec.universe)
    if total > 1 + _EPS:
        issues.append(Issue(3, "universe", f"비중 합이 1을 넘습니다 ({total:g}). 레버리지는 지원하지 않습니다."))

    if spec.entry is None or not spec.entry.conditions:
        issues.append(Issue(3, "entry.conditions", "매수 조건이 하나 이상 있어야 합니다.",
                            kind="clarify", question=_question("no_entry")))
    ex = spec.exit
    if ex is None or not (ex.conditions or ex.stop_loss is not None or ex.take_profit is not None
                          or ex.max_hold_days is not None):
        # 판정 원칙: 매도 조건 없음 → 먼저 묻는다. NO_EXIT_RULE reject 는 사용자가 답을 거부한 경우(LLM 판정)뿐.
        issues.append(Issue(3, "exit", "매도 조건·손절·익절·보유기간 중 하나 이상 있어야 합니다.",
                            kind="clarify", question=_question("no_exit")))

    if spec.backtest.start >= spec.backtest.end:   # YYYY-MM-DD 문자열 비교 = 날짜 비교
        issues.append(Issue(3, "backtest.start",
                            f"시작일({spec.backtest.start})이 종료일({spec.backtest.end})보다 앞서야 합니다."))

    for path, c in _iter_conditions(spec):
        if isinstance(c.right, str) and c.right == c.left:
            issues.append(Issue(3, path, f"{c.left} 를 자기 자신과 비교하면 항상 거짓입니다."))
            continue
        fl, fr = _family(c.left), _family(c.right)
        if fr is not None and fl != fr:
            issues.append(Issue(3, path, f"단위가 다른 값끼리 비교합니다: {INDICATORS[c.left]['label']} vs "
                                         f"{INDICATORS[c.right.ref if isinstance(c.right, RefMul) else c.right]['label']}"))

    for side in ("entry", "exit"):
        rule = getattr(spec, side)
        if rule is None or rule.logic != "all":
            continue
        conds = rule.conditions
        for i in range(len(conds)):
            for j in range(i + 1, len(conds)):
                if _contradicts(conds[i], conds[j]):
                    issues.append(Issue(3, f"{side}.conditions[{j}]",
                                        f"{side}.conditions[{i}] 와 동시에 참일 수 없어 조건이 영원히 성립하지 않습니다."))

    # 매수 조건이 성립하면 매도 조건도 항상 성립 → 매수 다음 날 바로 매도하는 모순
    if spec.entry and ex and spec.entry.conditions and ex.conditions:
        ent, exc = spec.entry.conditions, ex.conditions
        if spec.entry.logic == "all" and ex.logic == "any":
            hit = next(((i, j) for j, b in enumerate(exc) for i, a in enumerate(ent) if _implies(a, b)), None)
        elif spec.entry.logic == "all" and ex.logic == "all":
            hit = (0, 0) if all(any(_implies(a, b) for a in ent) for b in exc) else None
        else:  # entry any: 모든 매수 조건 각각이 어떤 매도 조건을 함의해야 함
            ok = all(any(_implies(a, b) for b in exc) for a in ent) if ex.logic == "any" else False
            hit = (0, 0) if ok else None
        if hit is not None:
            issues.append(Issue(3, f"exit.conditions[{hit[1]}]",
                                "매수 조건이 참인 날에는 이 매도 조건도 항상 참입니다 (매수·매도 조건 모순)."))
    return issues


# ---------------------------------------------------------------- 4. 범위

def _stage_range(spec: DemoSpec) -> list[Issue]:
    issues: list[Issue] = []
    if len(spec.universe) > LIMITS["max_universe"]:
        issues.append(Issue(4, "universe", f"종목은 최대 {LIMITS['max_universe']}개입니다 (현재 {len(spec.universe)}개)."))
    for i, u in enumerate(spec.universe):
        if not (0 < u.weight <= 1):
            issues.append(Issue(4, f"universe[{i}].weight", f"비중은 0 초과 1 이하여야 합니다 ({u.weight:g})."))
    ex = spec.exit
    if ex is not None:
        for name, label in (("stop_loss", "손절"), ("take_profit", "익절")):
            v = getattr(ex, name)
            lo, hi = LIMITS[name]
            if v is not None and not (lo < v <= hi):
                issues.append(Issue(4, f"exit.{name}",
                                    f"{label} 비율은 0 초과 {hi:g} 이하의 소수입니다 (예: 0.05 = 5%). 현재 {v:g}."))
        if ex.max_hold_days is not None:
            lo, hi = LIMITS["max_hold_days"]
            if not (lo <= ex.max_hold_days <= hi):
                issues.append(Issue(4, "exit.max_hold_days",
                                    f"보유기간은 {lo}~{hi} 거래일입니다. 현재 {ex.max_hold_days}."))
    for path, c in _iter_conditions(spec):
        rng = INDICATORS[c.left].get("range")
        if rng and isinstance(c.right, (int, float)) and not (rng[0] <= c.right <= rng[1]):
            issues.append(Issue(4, f"{path}.right",
                                f"{INDICATORS[c.left]['label']} 는 {rng[0]:g}~{rng[1]:g} 사이 값입니다. 현재 {c.right:g}."))
        if isinstance(c.right, RefMul) and c.right.mul <= 0:
            issues.append(Issue(4, f"{path}.right.mul", f"배수는 0보다 커야 합니다. 현재 {c.right.mul:g}."))
    if spec.backtest.initial_cash <= 0:
        issues.append(Issue(4, "backtest.initial_cash", "초기 자금은 0보다 커야 합니다."))
    if spec.costs.commission_bps < 0:
        issues.append(Issue(4, "costs.commission_bps", "수수료는 음수일 수 없습니다."))
    return issues


# ---------------------------------------------------------------- 진입점

def validate(data: Any, source_text: Optional[str] = None) -> ValidationReport:
    """dict / JSON 문자열 / DemoSpec 을 받아 4단계 검증 결과를 반환한다.

    source_text: 원문 대조에 쓸 사용자 입력(원문 + clarify 답변). 없으면 provenance.input_text 를 쓰고,
    그것도 없으면(수기 작성 명세) 원문 대조를 생략한다."""
    spec, issues = _stage_format(data)
    stages = [StageResult(1, "failed" if issues else "passed", issues)]
    for n, fn in ((2, lambda sp: _stage_reference(sp, source_text)), (3, _stage_logic), (4, _stage_range)):
        if stages[-1].status != "passed":
            stages.append(StageResult(n, "skipped"))
            continue
        found = fn(spec)
        stages.append(StageResult(n, "failed" if found else "passed", found))
    return ValidationReport(stages=stages, spec=spec)
