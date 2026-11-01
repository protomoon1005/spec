"""전략서 검사 v0.2 (docs/strategy-spec-v0.2.md "전략서 검사"). AI 미사용.

  1. 모양      — 전략서 모양이 맞나 (Pydantic: 칸·타입·허용값·비중 0~1)
  2. 존재      — 종목이 목록에 있나, 지표·기간·누구의 값(of)이 허용 목록에 있나
  3. 말이 되는지 — 단위가 다른 비교, 범위 밖 숫자, 자기 자신과 비교, 팔 때 전용 지표를 살 때 씀(막힘)
                  사는 조건이면 파는 조건도 늘 참, 비중 합 > 100%, 최대 비중 충돌, 한쪽 조건만 있음(되묻기)
  4. 근거      — 아직 없음 (빈칸 채우기 단계와 함께 넣는다)
  5. 시험 실행  — 아직 없음 (백테스트 엔진의 warnings 가 같은 내용을 낸다)

결과 종류: block(막힘 → 거절) · clarify(되묻기) · warn(경고, 진행).
앞 단계에서 막히면 뒤 단계는 돌리지 않는다(skipped). 되묻기·경고만 있으면 다음 단계로 간다.

2단계의 "지표 계산에 필요한 과거 데이터까지 있는가(NO_DATA)"는 가격 데이터를 읽어야 해서 여기서 하지 않고,
백테스트 엔진이 구간 가격이 없으면 ValueError(NO_DATA)로 멈춘다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from pydantic import ValidationError

from core.dictionary import INDEX_SYMBOLS, INDICATORS, indicator_label, load_tickers, of_kind
from core.schema import AllOf, AnyOf, Compare, Cond, Operand, Strategy, Value, iter_compares

STAGES = {1: "모양", 2: "존재", 3: "말이 되는지", 4: "근거", 5: "시험 실행"}
IMPLEMENTED = (1, 2, 3)
_EPS = 1e-9


@dataclass
class Issue:
    stage: int
    field: str
    message: str
    kind: str = "block"                  # "block" | "clarify" | "warn"
    code: str = "SHAPE"
    question: Optional[str] = None

    def __str__(self) -> str:
        return f"[{self.stage}단계 {STAGES[self.stage]}] {self.field}: {self.message} ({self.code})"


@dataclass
class StageResult:
    stage: int
    status: str        # passed | failed(막힘) | questions(되묻기) | warned | skipped | not_implemented
    issues: list[Issue] = field(default_factory=list)

    @property
    def name(self) -> str:
        return STAGES[self.stage]


@dataclass
class ValidationReport:
    stages: list[StageResult]
    spec: Optional[Strategy]

    @property
    def issues(self) -> list[Issue]:
        return [i for s in self.stages for i in s.issues]

    @property
    def verdict(self) -> str:
        """ok | clarify | reject. 막힘이 하나라도 있으면 reject(VALIDATION)."""
        kinds = {i.kind for i in self.issues}
        if self.spec is None or "block" in kinds:
            return "reject"
        if "clarify" in kinds:
            return "clarify"
        return "ok"

    @property
    def ok(self) -> bool:
        return self.verdict == "ok"

    @property
    def failed_stage(self) -> Optional[int]:
        return next((s.stage for s in self.stages if s.status == "failed"), None)

    @property
    def questions(self) -> list[str]:
        return list(dict.fromkeys(i.question for i in self.issues if i.kind == "clarify" and i.question))[:3]


# ---------------------------------------------------------------- 1. 모양

def _loc(loc: tuple) -> str:
    out = ""
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        elif part in ("Compare", "AllOf", "AnyOf", "Operand", "Value") or not str(part).isidentifier():
            continue
        else:
            out += f".{part}" if out else str(part)
    return out or "(최상위)"


def _stage_shape(data: Any) -> tuple[Optional[Strategy], list[Issue]]:
    if isinstance(data, Strategy):
        return data, []
    if isinstance(data, (str, bytes)):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as e:
            return None, [Issue(1, "(최상위)", f"JSON 으로 읽을 수 없습니다: {e.msg}")]
    try:
        return Strategy.model_validate(data), []
    except ValidationError as e:
        found: dict[str, Issue] = {}
        for err in e.errors():
            path = _loc(err["loc"])
            found.setdefault(path, Issue(1, path, f"{err['msg']} (입력: {err.get('input')!r})"))
        return None, list(found.values())


# ---------------------------------------------------------------- 2. 존재

def _all_compares(spec: Strategy):
    if spec.rules is None:
        return
    yield from iter_compares(spec.rules.entry, "rules.entry")
    yield from iter_compares(spec.rules.exit, "rules.exit")


def _stage_exist(spec: Strategy) -> list[Issue]:
    issues: list[Issue] = []
    tickers = load_tickers()
    for i, a in enumerate(spec.assets):
        info = tickers.get(a.ticker)
        if info is None:
            issues.append(Issue(2, f"assets[{i}].ticker", f"종목 목록에 없는 종목입니다: {a.ticker}", code="UNKNOWN_TICKER"))
        elif a.name != info.name:
            issues.append(Issue(2, f"assets[{i}].name", f"{a.ticker} 의 이름은 '{info.name}' 입니다: '{a.name}'",
                                code="NAME_MISMATCH"))
    for path, c in _all_compares(spec):
        for side in ("left", "right"):
            op = getattr(c, side)
            if not isinstance(op, Operand):
                continue
            meta = INDICATORS[op.indicator]
            where = f"{path}.{side}"
            if op.window not in meta["windows"]:
                allowed = "기간 없음" if meta["windows"] == [None] else "·".join(map(str, meta["windows"])) + "일"
                issues.append(Issue(2, where, f"{op.indicator} 에 쓸 수 있는 기간은 {allowed} 입니다 (현재 {op.window}).",
                                    code="BAD_WINDOW"))
            kind = of_kind(op.of)
            allowed_of = meta["of"]
            if kind == "ticker":
                if "ticker" not in allowed_of:
                    issues.append(Issue(2, where, f"{op.indicator} 는 다른 종목({op.of})의 값으로 쓸 수 없습니다.",
                                        code="BAD_OF"))
                elif op.of not in tickers:
                    issues.append(Issue(2, where, f"종목 목록에 없는 종목입니다: {op.of}", code="UNKNOWN_TICKER"))
            elif op.of not in allowed_of:
                ok = ", ".join(x if x != "ticker" else "다른 종목" for x in allowed_of)
                issues.append(Issue(2, where, f"{op.indicator} 는 '{op.of}' 의 값으로 쓸 수 없습니다 (가능: {ok}).",
                                    code="BAD_OF"))
    return issues


# ---------------------------------------------------------------- 3. 말이 되는지

def _unit(x) -> Optional[str]:
    return INDICATORS[x.indicator]["unit"] if isinstance(x, Operand) else None


_LOW = {"<", "<=", "cross_below"}     # 교차한 날엔 그 상태도 참
_HIGH = {">", ">=", "cross_above"}


def _implies_cmp(a: Compare, b: Compare) -> bool:
    """a 가 참인 날 b 도 반드시 참인가 (간단 규칙)."""
    if a == b:
        return True
    if b.op not in ("<", "<=", ">", ">=") or a.left != b.left:
        return False
    side = _LOW if b.op in ("<", "<=") else _HIGH
    if a.op not in side:
        return False
    if isinstance(a.right, Value) and isinstance(b.right, Value):
        av, bv = a.right.value, b.right.value
        strict_b = b.op in ("<", ">")
        inclusive_a = a.op in ("<=", ">=")
        if b.op in ("<", "<="):
            return av < bv or (av == bv and not (strict_b and inclusive_a))
        return av > bv or (av == bv and not (strict_b and inclusive_a))
    return a.right == b.right and not (b.op in ("<", ">") and a.op in ("<=", ">="))


def _implies(a: Cond, b: Cond) -> bool:
    if isinstance(b, AllOf):
        return all(_implies(a, x) for x in b.all)
    if isinstance(b, AnyOf):
        return any(_implies(a, x) for x in b.any)
    if isinstance(a, Compare):
        return _implies_cmp(a, b)
    if isinstance(a, AllOf):
        return any(_implies(x, b) for x in a.all)
    return all(_implies(x, b) for x in a.any)


def _pct(w: float) -> str:
    return f"{round(w * 100, 4):g}%"


def _stage_logic(spec: Strategy) -> list[Issue]:
    issues: list[Issue] = []
    if spec.backtest.start >= spec.backtest.end:
        issues.append(Issue(3, "backtest.start", f"시작일({spec.backtest.start})이 종료일({spec.backtest.end})보다 "
                                                 f"앞서야 합니다.", code="DATE_ORDER"))
    seen = set()
    for i, a in enumerate(spec.assets):
        if a.ticker in seen:
            issues.append(Issue(3, f"assets[{i}]", f"같은 종목이 두 번 있습니다: {a.ticker}", code="DUPLICATE"))
        seen.add(a.ticker)

    ws = [a.weight for a in spec.assets]
    if any(w is None for w in ws) and any(w is not None for w in ws):
        issues.append(Issue(3, "assets", "일부 종목에만 비중이 있습니다.", kind="clarify", code="PARTIAL_WEIGHTS",
                            question="비중을 말하지 않은 종목은 어떻게 할까요 (남은 비중을 똑같이 나누기 / 직접 입력)?"))
    elif all(w is not None for w in ws):
        total = sum(ws)
        if total > 1 + _EPS:
            issues.append(Issue(3, "assets", f"비중 합이 100%를 넘습니다 ({_pct(total)}).", kind="clarify",
                                code="WEIGHT_SUM", question=f"비중 합이 {_pct(total)}예요. 어떻게 나눌까요?"))
    if spec.max_weight is not None:
        for i, a in enumerate(spec.assets):
            if a.weight is not None and a.weight > spec.max_weight + _EPS:
                issues.append(Issue(3, f"assets[{i}].weight",
                                    f"{a.name} 비중 {_pct(a.weight)}가 종목당 최대 비중 {_pct(spec.max_weight)}보다 큽니다.",
                                    kind="clarify", code="WEIGHT_OVER_MAX",
                                    question=f"{a.name}을 {_pct(a.weight)} 담을까요, 최대 비중 {_pct(spec.max_weight)}에 맞출까요?"))

    if spec.rules is None:
        if spec.rebalance is None:
            issues.append(Issue(3, "rebalance", "조건이 없는 전략은 리밸런싱 주기가 있어야 합니다.", code="NO_REBALANCE"))
        return issues

    r = spec.rules
    if r.entry is None:
        issues.append(Issue(3, "rules.entry", "살 때 조건이 없습니다.", kind="clarify", code="NO_ENTRY",
                            question="언제 살까요 (처음부터 갖고 시작 / 조건이 반대가 되면 다시 사기)?"))
    if r.exit is None:
        issues.append(Issue(3, "rules.exit", "팔 때 조건이 없습니다.", kind="clarify", code="NO_EXIT",
                            question="언제 팔까요 (조건이 반대가 되면 팔기 / 몇 % 오르면 팔기 / 계속 갖고 있기)?"))

    for path, c in _all_compares(spec):
        l, rt = c.left, c.right
        if path.startswith("rules.entry"):
            for op in (l, rt):
                if isinstance(op, Operand) and INDICATORS[op.indicator].get("exit_only"):
                    issues.append(Issue(3, path, f"{indicator_label(op.indicator, op.window)}는 산 뒤에만 있는 값이라 "
                                                 f"살 때 조건에 쓸 수 없습니다.", code="EXIT_ONLY_IN_ENTRY"))
        if isinstance(rt, Operand) and rt == l:
            issues.append(Issue(3, path, "같은 값끼리 비교하면 조건이 늘 같은 결과입니다.", code="SELF_COMPARE"))
            continue
        ul, ur = _unit(l), _unit(rt)
        if ur is not None and ul != ur:
            issues.append(Issue(3, path, f"단위가 다른 값끼리 비교합니다: {indicator_label(l.indicator, l.window)} vs "
                                         f"{indicator_label(rt.indicator, rt.window)}", code="UNIT_MISMATCH"))
        if isinstance(rt, Value):
            lo, hi = INDICATORS[l.indicator]["range"]
            v = rt.value
            if (lo is not None and v < lo) or (hi is not None and v > hi):
                rng = f"{lo if lo is not None else ''}~{hi if hi is not None else ''}"
                issues.append(Issue(3, f"{path}.right", f"{indicator_label(l.indicator, l.window)}가 가질 수 없는 값입니다: "
                                                        f"{v:g} (범위 {rng})", code="OUT_OF_RANGE"))

    if r.entry is not None and r.exit is not None and _implies(r.entry, r.exit):
        issues.append(Issue(3, "rules.exit", "사는 조건이 참인 날에는 파는 조건도 늘 참입니다.", kind="clarify",
                            code="ALWAYS_BOTH",
                            question="사는 조건이 맞는 날 파는 조건도 항상 맞아요. 파는 조건을 어떻게 바꿀까요?"))
    return issues


# ---------------------------------------------------------------- 진입점

def _status(issues: list[Issue]) -> str:
    kinds = {i.kind for i in issues}
    if "block" in kinds:
        return "failed"
    if "clarify" in kinds:
        return "questions"
    if "warn" in kinds:
        return "warned"
    return "passed"


def validate(data: Any) -> ValidationReport:
    """dict / JSON 문자열 / Strategy 를 받아 검사 결과를 낸다 (1~3단계 실행, 4·5단계는 아직 없음)."""
    spec, issues = _stage_shape(data)
    stages = [StageResult(1, "failed" if issues else "passed", [*issues])]
    for n, fn in ((2, _stage_exist), (3, _stage_logic)):
        if stages[-1].status == "failed" or stages[-1].status == "skipped":
            stages.append(StageResult(n, "skipped"))
            continue
        found = fn(spec)
        stages.append(StageResult(n, _status(found), found))
    blocked = any(s.status in ("failed", "skipped") for s in stages)
    for n in (4, 5):
        stages.append(StageResult(n, "skipped" if blocked else "not_implemented"))
    return ValidationReport(stages=stages, spec=spec)
