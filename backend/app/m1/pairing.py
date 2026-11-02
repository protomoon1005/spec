# AI 짝짓기와 근거 검사 (전략서 v0.2 명세화 2번째 일, 검사 4단계).
#
# 설명을 docstring 이 아니라 주석에 두는 이유는 같은 폴더의 다른 파일과 같다(as_of 가드 사정권).
#
# 설계 문서 "AI 짝짓기 출력", "4단계. 근거 검사"(2026-11-02).
#   - AI 에게는 문장과 조각 목록("1:삼성전자" ...)만 주고, 어느 조각이 어느 빈칸인지 짝짓게 한다
#   - 칸마다 고를 수 있는 값을 조각 종류로 좁힌 enum 으로 준다(left 에는 지표 조각만 등)
#   - 재귀 구조를 쓰지 않는다. 조건은 join(one, all, any) + items 한 겹이다
#   - AI 는 값을 쓰지 않는다. 값은 다음 단계(값 바꾸기)가 조각의 meaning 에서 만든다
#   - AI 는 빈칸 상태를 적지 않는다. 사전 밖의 말만 quotes 에 원문을 인용해 표시한다
# 근거 검사(검사 4단계)
#   - 지어낸 값: 조각 목록에 없는 값, 원문에 없는 인용
#   - 빠뜨린 내용: 어느 칸에도 쓰이지 않은 조각. 숫자도 조각이라 같이 잡힌다.
#     조각을 인용으로 넘긴 것도 빠뜨린 것이다
#   - 걸리면 걸린 내용을 프롬프트 끝에 붙여 한 번만 다시 짝짓는다. 같은 문장이면 같은 피드백이라 결과도 같다
#   - 그래도 걸리면 되묻기 거리로 넘긴다(판정은 다음 단계)
# 받은 짝짓기는 normalize 가 규칙으로 한 모양으로 맞춘다(아래 "짝짓기 결과 정리")
# Ollama 의 스키마 강제는 약해서 enum 밖 값이 실제로 나온다(app/llm/ollama_client.py).
# 그래서 받은 뒤 다시 확인한다.
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from app.m1.spans import Span, SpanResult, negated_span_ids

MAX_RULES = 4
MAX_ITEMS = 4

# 칸마다 들어갈 수 있는 조각 종류
FIELD_KINDS: dict[str, tuple[str, ...]] = {
    "universe": ("instrument",),
    "target": ("instrument",),
    "subject": ("subject", "instrument"),
    "left": ("indicator",),
    "op": ("verb",),
    "right": ("number", "indicator"),
    "action": ("action",),
    "weight_asset": ("instrument",),
    "weight_value": ("number", "weight"),
    "rebalance": ("rebalance",),
    "max_weight": ("weight", "number"),
    "period": ("period",),
    "capital": ("capital",),
    "dividends": ("dividend",),
    "selection": ("sector", "selection", "number", "product", "subject"),
}
KIND_LABELS = {
    "instrument": "종목", "indicator": "지표", "number": "숫자", "verb": "비교", "action": "사기·팔기",
    "subject": "시장·지수", "weight": "비중", "rebalance": "리밸런싱", "period": "기간", "capital": "금액",
    "dividend": "배당", "selection": "고르기", "sector": "업종", "product": "상품", "negation": "부정",
    "reject": "거절", "intent": "의도",
}
# 짝짓지 않아도 되는 조각: 부정어(규칙이 처리), 방향 없는 "면"
EXEMPT_ENTRY_IDS = ("negation", "bare_if")
# 짝지어도 되고 안 지어도 되는 조각(아래 _optional_ids)
OPTIONAL_ENTRY_IDS = ("rb_word",)
# 비교 대상만 말해도("20일 이평 위면") 종가와 비교하는 지표
LEVEL_INDICATORS = ("ma",)
QUOTE_STATES = ("ambiguous", "unsupported")

SYSTEM_PROMPT = (
    "너는 한국어 투자 전략 문장의 조각을 전략서 빈칸에 짝짓는 도구다. "
    "값은 주어진 조각 목록에서만 고른다. 목록에 없는 값을 지어내지 않는다. "
    "모든 조각을 어느 칸에든 쓴다. 설명하지 말고 JSON 만 낸다."
)

EXAMPLE_SENTENCE = "삼성전자 RSI 30 아래로 떨어지면 사고 5일선 깨지면 팔아줘"
EXAMPLE_SPANS = (
    "1:삼성전자 [종목]\n2:RSI [지표]\n3:30 [숫자]\n4:아래로 떨어지면 [비교]\n5:사고 [사기·팔기]\n"
    "6:5일선 [지표]\n7:깨지면 [비교]\n8:팔아줘 [사기·팔기]"
)
EXAMPLE_OUTPUT = {
    "universe": ["1:삼성전자"],
    "weights": [],
    "rebalance": [],
    "rules": [{
        "target": "each",
        "entry": {"actions": ["5:사고"], "join": "one",
                  "items": [{"subject": None, "left": "2:RSI", "op": "4:아래로 떨어지면", "right": "3:30"}]},
        "exit": {"actions": ["8:팔아줘"], "join": "one",
                 "items": [{"subject": None, "left": None, "op": "7:깨지면", "right": "6:5일선"}]},
    }],
    "max_weight": None,
    "backtest": {"period": [], "capital": [], "dividends": []},
    "selection": [],
    "quotes": [],
}

GUIDE = "\n".join([
    "칸 설명",
    "- universe: 사고팔 종목 조각",
    "- rules: 사고파는 조건. entry 는 살 때, exit 는 팔 때. 조건이 없으면 null",
    "  - actions: 그 조건의 사기 · 팔기 조각. 여럿이면 모두",
    "  - items: 비교 하나씩. left 는 지표, op 는 비교하는 말, right 는 숫자나 지표, subject 는 누구의 값인지(코스피 같은 시장 · 지수나 다른 종목. 그 종목 자신이면 null)",  # noqa: E501 (프롬프트 한 줄. 바꾸면 AI 결과가 달라진다)
    "  - 비교가 둘 이상이면 join 에 all(그리고) 또는 any(또는). 하나면 one",
    '  - 같은 조각을 여러 번 써도 된다. "20일 이평 돌파하면 사고 깨지면 팔아" 처럼 팔 때 지표를 생략하면 살 때 조각을 다시 쓴다',  # noqa: E501 (프롬프트 한 줄. 바꾸면 AI 결과가 달라진다)
    '  - "5일선 깨지면" 처럼 무엇이 깨지는지 말하지 않으면 left 는 null 이다',
    "  - 종목마다 조건이 다르면 rule 을 종목마다 만들고 target 에 그 종목 조각을 쓴다. 모든 종목에 같은 조건이면 target 은 each",  # noqa: E501 (프롬프트 한 줄. 바꾸면 AI 결과가 달라진다)
    "  - 팔 조건이 여럿이면(익절과 손절, 이평 이탈과 VIX) exit 하나에 join 을 any 로 하고 비교를 모두 넣는다",
    '- weights: 비중. asset 에 종목 조각(모든 종목이면 null), value 에 비중 숫자나 "반반" · "똑같이" 같은 조각',  # noqa: E501 (프롬프트 한 줄. 바꾸면 AI 결과가 달라진다)
    "- rebalance: 리밸런싱 조각",
    "- max_weight: 종목당 최대 비중 조각",
    "- backtest: period 기간, capital 시작 금액, dividends 배당 조각",
    "- selection: 업종 · 고르는 방식 · 개수 조각",
    "- quotes: 조각 목록에 없지만 전략에 중요한 말만. 원문 글자 그대로 적는다. 조각 목록에 있는 말은 인용하지 말고 칸에 넣는다. 뜻이 하나로 정해지지 않으면 state 를 ambiguous, 다룰 수 없는 것이면 unsupported. slot 에는 관련 칸 이름",  # noqa: E501 (프롬프트 한 줄. 바꾸면 AI 결과가 달라진다)
])


@dataclass(frozen=True)
class Item:
    subject: Span | None
    left: Span | None
    op: Span | None
    right: Span | None

    def spans(self) -> list[Span]:
        return [s for s in (self.subject, self.left, self.op, self.right) if s is not None]


@dataclass(frozen=True)
class Condition:
    actions: tuple[Span, ...]  # 그 조건의 사기 · 팔기 조각. "익절 … 손절" 처럼 여럿일 수 있다
    join: str
    items: tuple[Item, ...]

    def spans(self) -> list[Span]:
        out = list(self.actions)
        for item in self.items:
            out.extend(item.spans())
        return out


@dataclass(frozen=True)
class RulePairing:
    target: Span | None  # None 이면 each
    entry: Condition | None
    exit: Condition | None


@dataclass(frozen=True)
class Quote:
    text: str
    slot: str
    state: str


@dataclass
class Pairing:
    universe: list[Span] = field(default_factory=list)
    weights: list[tuple[Span | None, Span | None]] = field(default_factory=list)
    rebalance: list[Span] = field(default_factory=list)
    rules: list[RulePairing] = field(default_factory=list)
    max_weight: Span | None = None
    period: list[Span] = field(default_factory=list)
    capital: list[Span] = field(default_factory=list)
    dividends: list[Span] = field(default_factory=list)
    selection: list[Span] = field(default_factory=list)
    quotes: list[Quote] = field(default_factory=list)
    invalid_labels: list[str] = field(default_factory=list)

    def used_span_ids(self) -> set[int]:
        spans: list[Span] = [*self.universe, *self.rebalance, *self.period, *self.capital, *self.dividends,
                             *self.selection]
        for asset, value in self.weights:
            spans.extend(s for s in (asset, value) if s is not None)
        if self.max_weight is not None:
            spans.append(self.max_weight)
        for rule in self.rules:
            if rule.target is not None:
                spans.append(rule.target)
            for cond in (rule.entry, rule.exit):
                if cond is not None:
                    spans.extend(cond.spans())
        return {s.id for s in spans}


@dataclass(frozen=True)
class EvidenceIssue:
    code: str  # unused_span · invalid_label · quote_not_in_text · quoted_span
    text: str
    span_id: int | None = None

    def message(self) -> str:
        if self.code == "unused_span":
            return f"조각 [{self.span_id}] '{self.text}' 이 어느 칸에도 쓰이지 않았다"
        if self.code == "invalid_label":
            return f"'{self.text}' 는 조각 목록에 없는 값이다"
        if self.code == "quoted_span":
            return f"조각 [{self.span_id}] '{self.text}' 는 인용하지 말고 알맞은 칸에 넣는다"
        return f"인용 '{self.text}' 는 문장에 그대로 있지 않다"


@dataclass(frozen=True)
class PairingOutcome:
    pairing: Pairing
    issues: tuple[EvidenceIssue, ...]  # 마지막 시도의 근거 검사 결과. 비어 있으면 통과
    first_issues: tuple[EvidenceIssue, ...]
    retried: bool
    raw: tuple[dict, ...]  # 시도마다 AI 가 낸 JSON


# ── 스키마와 프롬프트 ─────────────────────────────────────────────────


def labels_for(result: SpanResult, field_name: str, *, exclude: Iterable[int] = ()) -> list[str]:
    excluded = set(exclude)
    kinds = FIELD_KINDS[field_name]
    return [
        s.label() for s in result.spans
        if s.kind in kinds and s.id not in excluded and s.entry_id not in EXEMPT_ENTRY_IDS
    ]


def build_schema(result: SpanResult) -> dict:
    skip = _skipped_ids(result)

    def one(field_name: str) -> dict:
        labels = labels_for(result, field_name, exclude=skip)
        if not labels:
            return {"type": "null"}
        return {"anyOf": [{"type": "null"}, {"type": "string", "enum": labels}]}

    def many(field_name: str) -> dict:
        labels = labels_for(result, field_name, exclude=skip)
        if not labels:
            return {"type": "array", "maxItems": 0}
        return {"type": "array", "items": {"type": "string", "enum": labels}}

    target_labels = ["each", *labels_for(result, "target", exclude=skip)]
    item = _object({"subject": one("subject"), "left": one("left"), "op": one("op"), "right": one("right")})
    condition = {"anyOf": [{"type": "null"}, _object({
        "actions": many("action"),
        "join": {"type": "string", "enum": ["one", "all", "any"]},
        "items": {"type": "array", "items": item, "minItems": 1, "maxItems": MAX_ITEMS},
    })]}
    rule = _object({
        "target": {"type": "string", "enum": target_labels}, "entry": condition, "exit": condition,
    })
    weight = _object({"asset": one("weight_asset"), "value": one("weight_value")})
    quote = _object({
        "text": {"type": "string"},
        "slot": {"type": "string"},
        "state": {"type": "string", "enum": list(QUOTE_STATES)},
    })
    return _object({
        "universe": many("universe"),
        "weights": {"type": "array", "items": weight},
        "rebalance": many("rebalance"),
        "rules": {"type": "array", "items": rule, "maxItems": MAX_RULES},
        "max_weight": one("max_weight"),
        "backtest": _object({
            "period": many("period"), "capital": many("capital"), "dividends": many("dividends"),
        }),
        "selection": many("selection"),
        "quotes": {"type": "array", "items": quote},
    })


def _object(properties: dict) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def build_prompt(result: SpanResult, feedback: Sequence[str] = ()) -> str:
    skip = _skipped_ids(result)
    listed = "\n".join(
        f"{s.label()} [{KIND_LABELS[s.kind]}]" for s in result.spans
        if s.id not in skip and s.entry_id not in EXEMPT_ENTRY_IDS
    )
    parts = [
        GUIDE,
        "",
        "예시",
        f"문장: {EXAMPLE_SENTENCE}",
        "조각:",
        EXAMPLE_SPANS,
        "출력:",
        json.dumps(EXAMPLE_OUTPUT, ensure_ascii=False),
        "",
        f"문장: {result.text}",
        "조각:",
        listed or "(없음)",
    ]
    if feedback:
        parts += ["", "앞의 답에서 고칠 것:", *(f"- {line}" for line in feedback)]
    parts += ["출력:"]
    return "\n".join(parts)


def _skipped_ids(result: SpanResult) -> set[int]:
    # 부정된 조각, 거절 조각, 전략 의도 조각은 AI 에게 주지 않는다.
    # 거절 조각이 남아 있으면 짝짓기 전에 거절되고, 전략 의도 조각은 판정에만 쓴다
    return negated_span_ids(result) | {s.id for s in result.spans if s.kind in ("reject", "intent")}


# ── 받은 JSON 읽기와 근거 검사 ───────────────────────────────────────


def parse(raw: Mapping, result: SpanResult) -> Pairing:
    by_label = {s.label(): s for s in result.spans}
    out = Pairing()

    def span(value) -> Span | None:
        if value is None:
            return None
        found = by_label.get(str(value))
        if found is None:
            out.invalid_labels.append(str(value))
        return found

    def spans(values) -> list[Span]:
        return [s for s in (span(v) for v in (values or [])) if s is not None]

    def condition(value) -> Condition | None:
        if not isinstance(value, Mapping):
            return None
        items = tuple(
            Item(span(i.get("subject")), span(i.get("left")), span(i.get("op")), span(i.get("right")))
            for i in (value.get("items") or []) if isinstance(i, Mapping)
        )
        join = value.get("join") if value.get("join") in ("one", "all", "any") else "one"
        return Condition(tuple(spans(value.get("actions"))), join, items)

    out.universe = spans(raw.get("universe"))
    out.rebalance = spans(raw.get("rebalance"))
    out.selection = spans(raw.get("selection"))
    out.max_weight = span(raw.get("max_weight"))
    for w in raw.get("weights") or []:
        if isinstance(w, Mapping):
            out.weights.append((span(w.get("asset")), span(w.get("value"))))
    backtest = raw.get("backtest") if isinstance(raw.get("backtest"), Mapping) else {}
    out.period = spans(backtest.get("period"))
    out.capital = spans(backtest.get("capital"))
    out.dividends = spans(backtest.get("dividends"))
    for r in raw.get("rules") or []:
        if not isinstance(r, Mapping):
            continue
        target = None if r.get("target") in (None, "each") else span(r.get("target"))
        out.rules.append(RulePairing(target, condition(r.get("entry")), condition(r.get("exit"))))
    for q in raw.get("quotes") or []:
        if isinstance(q, Mapping) and q.get("text"):
            state = str(q.get("state") or "ambiguous")
            out.quotes.append(Quote(str(q["text"]), str(q.get("slot") or ""), state))
    return out


def evidence_issues(pairing: Pairing, result: SpanResult) -> list[EvidenceIssue]:
    issues = [EvidenceIssue("invalid_label", label) for label in pairing.invalid_labels]
    issues += [
        EvidenceIssue("quote_not_in_text", q.text) for q in pairing.quotes if q.text not in result.text
    ]
    # 인용은 조각 목록 밖의 말을 위한 길이다. 조각을 인용으로 넘기면 칸에 쓴 것으로 치지 않는다
    # (실제로 qwen3:8b 가 재시도 때 쓰지 않은 조각을 '애매함' 인용으로 넘겨 검사를 피했다, 2026-11-02)
    span_by_text = {s.text: s for s in result.spans}
    for q in pairing.quotes:
        quoted = span_by_text.get(q.text.strip())
        if quoted is not None:
            issues.append(EvidenceIssue("quoted_span", quoted.text, quoted.id))
    used = pairing.used_span_ids()
    skip = _skipped_ids(result) | _optional_ids(result, used)
    for s in result.spans:
        if s.id in used or s.id in skip or s.entry_id in EXEMPT_ENTRY_IDS:
            continue
        issues.append(EvidenceIssue("unused_span", s.text, s.id))
    return issues


def _optional_ids(result: SpanResult, used: set[int]) -> set[int]:
    # 써도 되고 안 써도 되는 조각(dev 측정에서 정함, 2026-11-02)
    #   - "리밸런싱" 같은 낱말: 주기 조각이 따로 있으면 할 일이 없고, 없으면 기본 주기라 뜻이 없다
    #   - 조건이 하나도 없는 문장(비교 · 지표 조각이 없음)의 사기 조각.
    #     "똑같이 나눠서 사두고" 는 비중 배분이다
    #   - 쓰인 동사 바로 옆의 같은 방향 동사: "넘게 터지고" 는 한 번의 올라가는 순간이다
    out = {s.id for s in result.spans if s.entry_id in OPTIONAL_ENTRY_IDS}
    spans = result.spans
    has_condition = any(s.kind == "indicator" or (s.kind == "verb" and s.meaning.get("op")) for s in spans)
    if not has_condition:
        out |= {s.id for s in spans if s.kind == "action"}
    for i, s in enumerate(spans):
        if s.kind != "verb" or s.id in used:
            continue
        for j in (i - 1, i + 1):
            if 0 <= j < len(spans):
                other = spans[j]
                gap = result.text[min(s.end, other.end):max(s.start, other.start)]
                if (other.kind == "verb" and other.id in used and not gap.strip()
                        and other.meaning.get("op") == s.meaning.get("op")):
                    out.add(s.id)
    return out


# ── 짝짓기 결과 정리 ────────────────────────────────────────────────
# AI 가 엉뚱하게 짝지은 것이 아니라, 같은 뜻을 다른 모양으로 낸 것을 규칙으로 한 모양으로 맞춘다
# (dev 측정에서 정함, 2026-11-02). AI 를 다시 부르지 않는다.
#   1. 네 칸이 다 빈 비교는 버린다. 비교가 없는 조건, 조건이 없는 rule 도 버린다
#   2. 이평처럼 비교 대상인 조각이 왼쪽에만 있거나 양쪽에 다 있으면 오른쪽으로 옮기고 왼쪽을 비운다
#      (왼쪽이 비면 종가). "20일 이평 위면" = 종가 > 20일 이평. 그 밖의 조각이 양쪽에 다 있으면 왼쪽에만 둔다
#   3. 왼쪽이 비고 오른쪽이 숫자면 생략된 지표다. 반대쪽 조건(없으면 같은 쪽 앞 비교)의 왼쪽을 이어받는다.
#      "RSI 30 밑이면 사고 70 넘으면 팔아" 의 70 은 RSI. 익절 · 손절은 산 가격 대비 수익률이라 이어받지 않는다
#   4. 비교가 둘 이상인데 join 이 one 이면 all 이다("위일 때만 … 밑이면 사고")
#   5. target 이 같은 rule 이 둘이면 하나로 합친다. 같은 쪽 조건이 둘 다 있으면 any 로 묶는다


def normalize(p: Pairing) -> Pairing:
    rules = [r for r in (_normalize_rule(r) for r in p.rules) if r is not None]
    merged: list[RulePairing] = []
    for rule in rules:
        same = next((m for m in merged if _target_id(m) == _target_id(rule)), None)
        if same is None:
            merged.append(rule)
            continue
        joined = RulePairing(same.target, _merge(same.entry, rule.entry), _merge(same.exit, rule.exit))
        if joined.entry is _UNMERGEABLE or joined.exit is _UNMERGEABLE:
            merged.append(rule)  # 합칠 수 없으면 둘 다 둔다. 검사가 막는다
        else:
            merged[merged.index(same)] = joined
    out = Pairing(**{k: getattr(p, k) for k in p.__dataclass_fields__})
    out.rules = merged
    return out


_UNMERGEABLE = Condition((), "any", ())


def _target_id(rule: RulePairing):
    return None if rule.target is None else rule.target.meaning.get("ticker", rule.target.id)


def _merge(a: Condition | None, b: Condition | None):
    if a is None or b is None:
        return a or b
    if a.join == "all" or b.join == "all":
        return _UNMERGEABLE
    return Condition(a.actions + b.actions, "any", a.items + b.items)


def _normalize_rule(rule: RulePairing) -> RulePairing | None:
    entry = _normalize_condition(rule.entry)
    exit_ = _normalize_condition(rule.exit)
    entry = _fill_omitted_left(entry, exit_)
    exit_ = _fill_omitted_left(exit_, entry)
    if entry is None and exit_ is None:
        return None
    return RulePairing(rule.target, entry, exit_)


def _normalize_condition(cond: Condition | None) -> Condition | None:
    if cond is None:
        return None
    items = []
    for item in cond.items:
        if not item.spans():
            continue
        left, right = item.left, item.right
        if left is not None and right is not None and left.id == right.id:
            # 같은 조각이 양쪽에: 이평 같은 비교 대상이면 오른쪽에(왼쪽은 종가), 아니면 왼쪽에 둔다.
            # "거래량이 20일 평균의 3배" 는 지표와 숫자를 같이 가져서 오른쪽 숫자는 값 바꾸기가 채운다
            if left.meaning.get("indicator") in LEVEL_INDICATORS:
                left = None
            else:
                right = None
        elif right is None and left is not None and left.meaning.get("indicator") in LEVEL_INDICATORS:
            left, right = None, left
        items.append(Item(item.subject, left, item.op, right))
    if not items:
        return None
    join = "all" if len(items) > 1 and cond.join == "one" else cond.join
    return Condition(cond.actions, join, tuple(items))


def _fill_omitted_left(cond: Condition | None, other: Condition | None) -> Condition | None:
    if cond is None:
        return None
    if any(a.meaning.get("exit_kind") for a in cond.actions):
        return cond  # 익절 · 손절은 산 가격 대비 수익률이다(값 바꾸기 단계)
    donors = [i for i in (other.items if other else ()) if i.left is not None]
    donors += [i for i in cond.items if i.left is not None]
    items = []
    for index, item in enumerate(cond.items):
        if item.left is None and item.right is not None and item.right.kind == "number" and donors:
            donor = donors[index] if index < len(donors) else donors[0]
            item = Item(item.subject or donor.subject, donor.left, item.op, item.right)
        items.append(item)
    return Condition(cond.actions, cond.join, tuple(items))


def pair(result: SpanResult, client, *, retry: bool = True) -> PairingOutcome:
    schema = build_schema(result)
    raw_first = client.generate_json(build_prompt(result), schema, system=SYSTEM_PROMPT)
    first = normalize(parse(raw_first, result))
    first_issues = tuple(evidence_issues(first, result))
    if not first_issues or not retry:
        return PairingOutcome(first, first_issues, first_issues, False, (raw_first,))
    feedback = [issue.message() for issue in first_issues]
    raw_second = client.generate_json(build_prompt(result, feedback), schema, system=SYSTEM_PROMPT)
    second = normalize(parse(raw_second, result))
    return PairingOutcome(
        second, tuple(evidence_issues(second, result)), first_issues, True, (raw_first, raw_second)
    )
