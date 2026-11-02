"""전략서 검사 v0.2 (docs/strategy-spec-v0.2.md "전략서 검사"). AI 미사용.

  1. 모양       — 전략서 모양이 맞나 (Pydantic: 칸·타입·허용값·비중 0~1)
  2. 존재       — 종목이 목록에 있나, 지표·기간·누구의 값(of)이 허용 목록에 있나,
                  백테스트 기간 가격과 지표 계산에 필요한 과거 데이터가 있나 (NO_DATA — 가격 데이터를 줄 때만)
  3. 말이 되는지 — 단위가 다른 비교, 범위 밖 숫자, 자기 자신과 비교, 팔 때 전용 지표를 살 때 씀(막힘)
                  사는 조건이면 파는 조건도 늘 참, 비중 합 > 100%, 최대 비중 충돌, 한쪽 조건만 있음(되묻기)
  4. 근거       — 말한 대로 적혔나: 지어낸 값 · 빠뜨린 내용 · 숫자 (core.grounding — 근거 정보를 줄 때만). 되묻기
  5. 시험 실행   — 백테스트 엔진으로 돌려 본다 (가격 데이터를 줄 때만). 경고만:
                  한 번도 안 삼 · 너무 잦은 매매 · 같은 날 사기·팔기 조건이 둘 다 참

결과 종류: block(막힘 → 거절) · clarify(되묻기) · warn(경고, 진행).
앞 단계에서 막히면 뒤 단계는 돌리지 않는다(skipped). 되묻기·경고만 있으면 다음 단계로 간다.
단계를 끄는 실험 기능: validate(..., stages=Stages.ALL & ~Stages.GROUNDING) → 그 단계는 skipped (note "꺼짐").
1단계는 끌 수 없다 (전략서를 읽어야 다른 단계를 돌릴 수 있다).
입력이 없어 돌릴 수 없는 단계(근거 정보 없음, 가격 데이터 없음)도 skipped 이고 note 에 이유를 적는다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from enum import IntFlag
from typing import Any, Callable, Optional, Union

import pandas as pd
from pydantic import ValidationError

from core.dictionary import (BENCHMARK_SYMBOL, INDEX_SYMBOLS, INDICATORS, US_SYMBOLS, indicator_label, load_tickers,
                             of_kind)
from core.grounding import Grounding
from core.indicators import lookback
from core.grounding import check as grounding_check
from core.schema import AllOf, AnyOf, Compare, Cond, Operand, Strategy, Value, iter_compares

STAGES = {1: "모양", 2: "존재", 3: "말이 되는지", 4: "근거", 5: "시험 실행"}
_EPS = 1e-9

# 5단계 "너무 자주 사고팜": 기간 거래일 대비 사고팔기 한 번(산 뒤 판 것, 미청산 포함)의 비율이 이 값 이상이면 경고.
# 0.2 = 평균 5거래일에 한 번 이상 사고팖. v0.2 에 숫자가 없어 데모에서 정함 (2026-11-02 팀 확정).
TOO_FREQUENT_RATIO = 0.2


class Stages(IntFlag):
    """켤 단계. 평가 때 4단계를 껐다 켜며 '근거 검사가 잡은 오류'를 잰다."""
    SHAPE = 1
    EXIST = 2
    LOGIC = 4
    GROUNDING = 8
    TRIAL = 16
    ALL = 31

    @classmethod
    def of(cls, n: int) -> "Stages":
        return cls(1 << (n - 1))


# 가격 데이터: {심볼: OHLCV} (테스트) 또는 심볼 → OHLCV 함수 (core.data.load_ohlcv: data/cache, 없으면 FDR 로 한 번 받음)
PriceSource = Union[dict, Callable[[str], pd.DataFrame]]


@dataclass
class Issue:
    stage: int
    field: str
    message: str
    kind: str = "block"                  # "block" | "clarify" | "warn"
    code: str = "SHAPE"
    question: Optional[str] = None
    options: Optional[list[str]] = None  # 되묻기 선택지 (없으면 '문장을 고쳐서 다시 입력'만)

    def __str__(self) -> str:
        return f"[{self.stage}단계 {STAGES[self.stage]}] {self.field}: {self.message} ({self.code})"

    @property
    def qid(self) -> str:
        """되묻기 질문 id (같은 문장·같은 전략서면 늘 같다)."""
        return f"validate:{self.code}:{self.field}"


@dataclass
class StageResult:
    stage: int
    status: str        # passed | failed(막힘) | questions(되묻기) | warned | skipped
    issues: list[Issue] = field(default_factory=list)
    note: str = ""     # skipped 이유 (꺼짐 / 앞 단계에서 막힘 / 가격 데이터 없음 …)

    @property
    def name(self) -> str:
        return STAGES[self.stage]


@dataclass
class ValidationReport:
    stages: list[StageResult]
    spec: Optional[Strategy]
    trial: Any = None                  # 5단계에서 돌린 BacktestResult

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
                            question="언제 살까요 (조건이 반대가 되면 다시 사기)?"))
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


# ---------------------------------------------------------------- 2. 존재 — 과거 데이터 (NO_DATA)

def _price_getter(prices: PriceSource) -> Callable[[str], Optional[pd.DataFrame]]:
    def get(sym: str) -> Optional[pd.DataFrame]:
        return prices.get(sym) if isinstance(prices, dict) else prices(sym)
    return get


def _symbol(of: str, ticker: str) -> str:
    return ticker if of == "self" else INDEX_SYMBOLS.get(of, of)


def _rule_operands(spec: Strategy):
    if spec.rules is None:
        return
    for _, c in [*iter_compares(spec.rules.entry, ""), *iter_compares(spec.rules.exit, "")]:
        for o in (c.left, c.right):
            if isinstance(o, Operand) and not INDICATORS[o.indicator].get("position"):
                yield c, o


def required_history(spec: Strategy) -> dict[str, tuple[int, str]]:
    """심볼 → (시작일 앞에 있어야 하는 거래일 수, 이유). 종목은 최소 1 (기간 시작 전에 상장돼 있어야 함).
    '~하는 순간'(cross)은 전날 값도 필요해 1을 더한다. v0.2 의 '120일 이동평균이면 시작일보다 120거래일 앞부터'를
    계산식대로 센다 (120일 이평: '~인 동안' 119, '~하는 순간' 120)."""
    need: dict[str, tuple[int, str]] = {a.ticker: (1, "기간 시작 전 상장") for a in spec.assets}
    for c, o in _rule_operands(spec):
        extra = 1 if c.op.startswith("cross") else 0
        n = lookback(o.indicator, o.window) + extra
        why = indicator_label(o.indicator, o.window) + (" (~하는 순간: 전날 값 포함)" if extra else "")
        for a in spec.assets:
            sym = _symbol(o.of, a.ticker)
            if n > need.get(sym, (-1, ""))[0]:
                need[sym] = (n, why)
    return need


def _stage_no_data(spec: Strategy, prices: PriceSource) -> list[Issue]:
    get = _price_getter(prices)
    start, end = pd.Timestamp(spec.backtest.start), pd.Timestamp(spec.backtest.end)
    issues: list[Issue] = []
    for sym, (n, why) in required_history(spec).items():
        try:
            df = get(sym)
        except Exception as e:  # noqa: BLE001 — 받지 못한 이유를 그대로 보여 준다
            issues.append(Issue(2, sym, f"{sym} 가격 데이터를 읽지 못했습니다: {e}", code="NO_DATA"))
            continue
        if df is None or not len(df):
            issues.append(Issue(2, sym, f"{sym} 가격 데이터가 없습니다.", code="NO_DATA"))
            continue
        idx = pd.DatetimeIndex(df.index)
        if sym not in US_SYMBOLS and not ((idx >= start) & (idx <= end)).any():
            issues.append(Issue(2, sym, f"{sym}: {spec.backtest.start}~{spec.backtest.end} 구간 가격이 없습니다.",
                                code="NO_DATA"))
            continue
        before = int((idx < start).sum())
        if before < n:
            issues.append(Issue(2, sym, f"{sym}: {why} 계산에 시작일({spec.backtest.start}) 앞 {n}거래일이 필요한데 "
                                        f"{before}거래일만 있습니다 (첫 데이터 {idx[0].date()}).", code="NO_DATA"))
    return issues


# ---------------------------------------------------------------- 4. 근거

def _stage_grounding(spec: Strategy, g: Grounding) -> list[Issue]:
    return [Issue(4, x.field, x.message, kind="clarify", code=x.code, question=x.question, options=x.options)
            for x in grounding_check(spec, g)]


# ---------------------------------------------------------------- 5. 시험 실행

def _stage_trial(spec: Strategy, prices: PriceSource) -> tuple[list[Issue], Any]:
    """백테스트 엔진(같은 코드)으로 돌려 본다. 이상해 보여도 사용자가 말한 그대로면 결과로 보여 주는 게
    서비스의 역할이라 막지 않고 경고만 한다."""
    from core.backtest import run_backtest      # 지연 import: backtest → validate 순환 방지
    try:
        if isinstance(prices, dict):
            data = prices
        else:
            get = _price_getter(prices)
            syms = {a.ticker for a in spec.assets} | {_symbol(o.of, a.ticker) for a in spec.assets
                                                      for _, o in _rule_operands(spec)}
            data = {s: get(s) for s in sorted(syms)}
            try:
                data[BENCHMARK_SYMBOL] = get(BENCHMARK_SYMBOL)
            except Exception:  # noqa: BLE001 — 비교 기준이 없어도 시험 실행은 한다
                pass
            data = {k: v for k, v in data.items() if v is not None}
        res = run_backtest(spec, data=data)
    except Exception as e:  # noqa: BLE001
        return [Issue(5, "(전략서)", f"시험 실행을 하지 못했습니다: {e}", kind="warn", code="TRIAL_ERROR")], None
    issues: list[Issue] = []
    if spec.rules is not None:
        if not any(f["side"] == "buy" for f in res.fills):
            issues.append(Issue(5, "rules.entry", "이 기간에 한 번도 사지 않습니다.", kind="warn", code="NEVER_BUY"))
        days = int((res.equity.index >= pd.Timestamp(spec.backtest.start)).sum()) or 1
        n = len(res.trades)
        if n and n / days >= TOO_FREQUENT_RATIO:
            cost = res.metrics["total_commission"] + res.metrics["total_tax"]
            issues.append(Issue(5, "rules", f"너무 자주 사고팝니다: {days}거래일 동안 {n}번 사고팔았습니다 "
                                            f"(평균 {days / n:.1f}거래일에 한 번). 수수료·세금 {cost:,.0f}원"
                                            f"(시작 금액의 {cost / spec.backtest.initial_cash:.1%})과 체결 오차가 "
                                            f"결과에 크게 영향을 줍니다.", kind="warn", code="TOO_FREQUENT"))
        if res.conflicts:
            issues.append(Issue(5, "rules", f"같은 날 살 조건과 팔 조건이 함께 참인 날이 {len(res.conflicts)}번 "
                                            f"있습니다. 그날은 팔기를 우선합니다.", kind="warn", code="SAME_DAY"))
    return issues, res


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


def validate(data: Any, *, stages: Stages = Stages.ALL, grounding: Optional[Grounding] = None,
             prices: Optional[PriceSource] = None) -> ValidationReport:
    """dict / JSON 문자열 / Strategy 를 받아 검사 결과를 낸다.
    grounding: 4단계 근거 검사에 쓸 원문·근거 (core.assemble.grounding). 없으면 4단계는 skipped.
    prices: 2단계 NO_DATA 와 5단계 시험 실행에 쓸 가격 데이터. 없으면 NO_DATA 는 보지 않고 5단계는 skipped."""
    spec, issues = _stage_shape(data)
    out = [StageResult(1, "failed" if issues else "passed", [*issues])]
    trial = None
    for n in (2, 3, 4, 5):
        if any(s.status == "failed" for s in out):
            out.append(StageResult(n, "skipped", note="앞 단계에서 막힘"))
            continue
        if not stages & Stages.of(n):
            out.append(StageResult(n, "skipped", note="꺼짐"))
            continue
        if n == 2:
            found = _stage_exist(spec)
            if prices is not None and not found:
                found = _stage_no_data(spec, prices)
        elif n == 3:
            found = _stage_logic(spec)
        elif n == 4:
            if grounding is None:
                out.append(StageResult(4, "skipped", note="근거 정보 없음"))
                continue
            found = _stage_grounding(spec, grounding)
        else:
            if prices is None:
                out.append(StageResult(5, "skipped", note="가격 데이터 없음"))
                continue
            found, trial = _stage_trial(spec, prices)
        out.append(StageResult(n, _status(found), found))
    return ValidationReport(stages=out, spec=spec, trial=trial)
