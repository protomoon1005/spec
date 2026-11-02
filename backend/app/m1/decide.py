# 판정, 되묻기 질문, 답 반영 (전략서 v0.2 명세화 4번째 일). 규칙만 쓰고 AI 는 쓰지 않는다.
#
# 설명을 docstring 이 아니라 주석에 두는 이유는 같은 폴더의 다른 파일과 같다(as_of 가드 사정권).
#
# 설계 문서 "판단하는 순서", "되묻는 순서", "되묻기 질문", "판정 결과 응답 모양"(2026-11-02).
# 판정
#   1. 거절 사전에 걸린 조각이 있으면 거절(부정어가 붙은 것은 뺀다). AI 를 부르기 전에 한다(precheck)
#   2. 전략 이야기 조각이 하나도 없으면 투자와 무관(OUT_OF_DOMAIN)으로 거절. 이것도 AI 전에 한다
#   3. 조립과 검사에서 나온 거절 거리가 있으면 문장에서 먼저 나온 것으로 거절
#   4. 되물을 거리가 있으면 순위(Ask.tier), 문장 위치 순으로 3개까지 묻는다
#      - 앞 질문의 답에 따라 사라지거나 바뀌는 질문은 다음 차례로 미룬다(_deferred)
#      - 2차례까지 묻는다. 두 번째 차례에 남은 거리가 3개를 넘거나, 2차례를 다 물었는데 남으면
#        끝내 못 정함(UNRESOLVED)으로 거절한다. 다 물어봐도 끝낼 수 없는 질문은 하지 않는다
#   5. 없으면 바로 진행
# 답 반영: 선택지를 고르면 그 선택지의 값을, 직접 입력하면 답 문장의 조각에서 값을 꺼내 overrides 에 넣는다.
# AI 를 다시 부르지 않고 조립(assemble)부터 다시 한다.
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from app.m1.assemble import Ask, Assembly, Reject, number_value
from app.m1.spans import SpanResult, negated_span_ids

MAX_QUESTIONS = 3
MAX_ROUNDS = 2
STRATEGY_KINDS = ("indicator", "action", "weight", "rebalance", "dividend", "sector", "selection", "intent")
REJECT_MESSAGES = {
    "FUTURE_REF": "판단하는 날에는 알 수 없는 미래 정보를 쓰는 조건이에요.",
    "UNKNOWN_INDICATOR": "아직 다루지 않는 지표예요.",
    "UNSUPPORTED_ASSET": "한국에 상장된 주식과 ETF 만 다뤄요.",
    "UNSUPPORTED_ACTION": "공매도, 빚내서 투자, 나눠 사기 같은 매매는 다루지 않아요.",
    "INTRADAY": "하루 안에서 시간을 보는 매매는 다루지 않아요. 하루에 한 번 종가로 판단해요.",
    "OUT_OF_DOMAIN": "투자 전략 이야기가 아니라서 도와드릴 수 없어요.",
    "NO_DATA": "백테스트 기간에 가격이 없는 종목이 있어요.",
    "VALIDATION": "전략서 검사를 통과하지 못했어요.",
    "UNRESOLVED": "두 번 여쭤봐도 정하지 못한 빈칸이 남았어요.",
}
INDICATOR_NAMES = {
    "rsi": "RSI",
    "ma": "이동평균",
    "ret": "수익률",
    "vol": "변동성",
    "volume_ratio": "거래량",
    "vix": "VIX",
    "close": "종가",
    "entry_return": "산 가격 대비 수익률",
    "holding_days": "보유 일수",
}


@dataclass(frozen=True)
class Choice:
    id: str
    label: str
    value: object  # overrides 에 들어갈 값. 화면에는 보내지 않는다


@dataclass(frozen=True)
class Question:
    id: str
    ask: Ask
    text: str
    choices: tuple[Choice, ...]
    allow_text: bool = True

    def public(self) -> dict:
        return {
            "id": self.id,
            "slot": self.ask.path,
            "kind": self.ask.kind,
            "text": self.text,
            "choices": [{"id": c.id, "label": c.label} for c in self.choices],
            "allow_text": self.allow_text,
        }


@dataclass(frozen=True)
class Rejection:
    code: str
    message: str
    evidence: str
    stage: int | None = None

    def public(self) -> dict:
        return {"code": self.code, "message": self.message, "evidence": self.evidence, "stage": self.stage}


@dataclass
class Decision:
    status: str  # ok · ask · reject
    questions: list[Question] = field(default_factory=list)
    rejection: Rejection | None = None
    all_asks: list[Ask] = field(default_factory=list)  # 미룬 것까지. 평가의 되묻기 재현율에 쓴다
    round: int = 0


# ── AI 전 판정 ───────────────────────────────────────────────────────


def precheck(result: SpanResult) -> Rejection | None:
    negated = negated_span_ids(result)
    for span in result.spans:
        if span.kind == "reject" and span.id not in negated:
            code = span.meaning["reject"]
            return Rejection(code, f"'{span.text}': {REJECT_MESSAGES[code]}", span.text)
    if not _talks_strategy(result):
        return Rejection("OUT_OF_DOMAIN", REJECT_MESSAGES["OUT_OF_DOMAIN"], result.text)
    return None


def _talks_strategy(result: SpanResult) -> bool:
    # 사기 · 팔기, 지표, 비교, 비중, 리밸런싱 같은 말이 하나라도 있거나,
    # 종목과 비중 숫자("삼성전자 50% SK하이닉스 40%")가 함께 있으면 전략 이야기다
    spans = result.spans
    if any(s.kind in STRATEGY_KINDS or (s.kind == "verb" and s.meaning.get("op")) for s in spans):
        return True
    has_instrument = any(s.kind == "instrument" for s in spans)
    has_weight_number = any(
        s.kind == "number" and s.meaning.get("unit") in ("percent", "ratio") for s in spans
    )
    return has_instrument and has_weight_number


# ── 조립 뒤 판정 ─────────────────────────────────────────────────────


def decide(
    assembly: Assembly,
    *,
    rounds_asked: int,
    extra_asks: Sequence[Ask] = (),
    extra_rejects: Sequence[Reject] = (),
    name_lookup: Callable[[str], list[tuple[str, str]]] | None = None,
) -> Decision:
    rejects = [*assembly.rejects, *extra_rejects]
    if rejects:
        first = min(rejects, key=lambda r: (r.stage is not None, r.position))
        # 검사에서 막힌 것은 검사가 쓴 문장을 그대로 보여 준다
        message = (
            first.evidence
            if first.stage is not None
            else f"'{first.evidence}': {REJECT_MESSAGES.get(first.code, '')}".strip()
        )
        return Decision(
            "reject",
            rejection=Rejection(
                first.code,
                message,
                first.evidence,
                first.stage,
            ),
        )
    asks = sorted([*assembly.asks, *extra_asks], key=lambda a: (a.tier, a.position, a.kind))
    if not asks:
        return Decision("ok")
    this_round = rounds_asked + 1
    if this_round > MAX_ROUNDS or (this_round == MAX_ROUNDS and len(asks) > MAX_QUESTIONS):
        left = ", ".join(_short(a) for a in asks)
        return Decision(
            "reject",
            all_asks=asks,
            rejection=Rejection("UNRESOLVED", f"{REJECT_MESSAGES['UNRESOLVED']} ({left})", left),
        )
    now = [a for a in asks if not _deferred(a, asks)][:MAX_QUESTIONS]
    questions = [question_for(a, f"q{i}", name_lookup) for i, a in enumerate(now, start=1)]
    return Decision("ask", questions=questions, all_asks=asks, round=this_round)


def _deferred(ask: Ask, asks: Sequence[Ask]) -> bool:
    # 앞 질문의 답에 따라 사라지거나 바뀌는 질문은 다음 차례로 미룬다(설계 문서 "되묻는 순서" 규칙 2)
    if ask.kind == "selection_needed":
        return any(a.kind == "sector_not_in_list" for a in asks)
    if ask.kind == "weight_vs_cap":
        return any(a.tier == 1 for a in asks)
    if ask.kind == "always_both_true":
        rule = ask.path.split(".")[0]
        return any(a.tier == 3 and a.path.startswith(rule) for a in asks)
    return False


def _short(ask: Ask) -> str:
    return ask.context.get("text") or ask.kind


# ── 질문 문구와 선택지 (설계 문서 "되묻기 질문") ─────────────────────


def question_for(ask: Ask, qid: str, name_lookup=None) -> Question:
    text, choices = _text_and_choices(ask, name_lookup)
    return Question(
        qid,
        ask,
        text,
        tuple(Choice(f"c{i}", label, value) for i, (label, value) in enumerate(choices, start=1)),
    )


def _text_and_choices(ask: Ask, name_lookup) -> tuple[str, list[tuple[str, object]]]:
    c = ask.context
    kind = ask.kind
    if kind == "universe":
        if c.get("reason") == "selector_not_in_demo":
            return "데모에서는 종목을 이름으로만 받아요. 어떤 종목으로 할까요?", []
        if c.get("has_rules"):
            return "어떤 종목에 이 조건을 적용할까요?", [("KODEX 200(069500)", ["069500"])]
        return "어떤 종목을 담을까요? 종목 이름이나 종목코드를 알려 주세요.", []
    if kind == "ambiguous_quote":
        if c.get("slot") == "universe":
            found = name_lookup(c["text"]) if name_lookup else []
            if found:
                return (
                    f"'{c['text']}' 에 해당하는 종목이 여러 개예요. 어느 것인가요?",
                    [(f"{name}({ticker})", [ticker]) for ticker, name in found[:4]],
                )
            return f"'{c['text']}' 을 찾지 못했어요. 정확한 이름이나 종목코드를 알려 주세요.", []
        return f"'{c['text']}' 를 어떻게 정할까요?", [("무시하기", "ignore")]
    if kind == "exit":
        return "언제 팔까요?", [
            ("살 조건과 반대가 되면 팔기", "reverse"),
            ("산 가격보다 10% 오르면 팔기", "take_profit_10"),
            ("팔지 않고 계속 갖고 있기", "hold"),
        ]
    if kind == "entry":
        return "언제 살까요?", [("팔 조건과 반대가 되면 사기", "reverse")]
    if kind == "ma_window":
        return "며칠 이동평균선인가요?", [("5일", 5), ("20일", 20), ("60일", 60)]
    if kind == "cross_windows":
        return (
            "어느 두 이동평균선이 교차하는 걸 말씀하시나요?",
            [("5일선과 20일선", (5, 20)), ("20일선과 60일선", (20, 60))],
        )
    if kind == "window_not_allowed":
        name, window, allowed = c["indicator"], c["window"], c["allowed"]
        if len(allowed) == 1:
            return (
                f"{INDICATOR_NAMES[name]}는 {allowed[0]}일만 써요. {allowed[0]}일로 할까요?",
                [(f"{allowed[0]}일", allowed[0])],
            )
        lower = max((w for w in allowed if w < window), default=None)
        upper = min((w for w in allowed if w > window), default=None)
        options = [w for w in (lower, upper) if w is not None]
        if name == "ma":
            said, names = f"{window}일선", "과 ".join(f"{w}일선" for w in options)
        else:
            said, names = f"{window}일 {INDICATOR_NAMES[name]}", "과 ".join(f"{w}일" for w in options)
        return f"{said}은 없어요. {names} 중 어느 쪽인가요?", [(f"{w}일", w) for w in options]
    if kind == "threshold":
        return _threshold(c)
    if kind == "direction":
        return "그 값보다 아래일 때인가요, 위일 때인가요?", [("아래일 때", "<"), ("위일 때", ">")]
    if kind == "indicator_missing":
        return "무엇이 그 값일 때를 말씀하시나요?", [("RSI(14일)", {"indicator": "rsi", "window": 14})]
    if kind == "weights_over_100":
        total = c["total"]
        return (f"비중 합이 {total:g}%예요. 어떻게 맞출까요?", [("비율대로 줄이기", "scale")])
    if kind == "rebalance_not_allowed":
        said = c.get("said", "")
        label = {"half_year": "반기마다", "yearly": "1년에 한 번"}.get(said, "그 주기")
        if said.startswith("drift"):
            return (
                "비중이 벗어나면 맞추는 건 5%p 만 써요. 어떻게 할까요?",
                [("5%p 벗어나면 맞추기", "RB_THRESHOLD_5"), ("매달 첫 거래일", "RB_MONTHLY_FIRST")],
            )
        return (
            f"{label}는 고를 수 없어요. 어느 주기로 할까요?",
            [("분기마다", "RB_QUARTERLY"), ("매달 첫 거래일", "RB_MONTHLY_FIRST")],
        )
    if kind == "evidence_unused":
        return f"'{c['text']}' 을 어떻게 반영할까요?", [("무시하기", "ignore")]
    if kind == "weight_vs_cap":
        return (
            f"지정한 비중이 종목당 최대 비중 {c.get('cap', 0) * 100:g}%보다 커요. 어떻게 할까요?",
            [("최대 비중까지만 담고 나머지는 현금", "cap")],
        )
    if kind == "always_both_true":
        return (
            f"사는 조건({c.get('entry', '')})이 맞는 날은 파는 조건({c.get('exit', '')})도 늘 맞아서 "
            "한 번도 살 수 없어요. 파는 조건을 어떻게 할까요?",
            [(c.get("flip_label", "방향 뒤집기"), "flip_exit")],
        )
    return f"{kind} 를 정해 주세요.", []


def _threshold(c: Mapping) -> tuple[str, list[tuple[str, object]]]:
    name, direction = c.get("indicator"), c.get("direction")
    label = INDICATOR_NAMES.get(name, "그 값")
    table = {
        ("rsi", "down"): [30, 20],
        ("rsi", "up"): [70, 80],
        ("ret", "up"): [0.05, 0.1, 0.2],
        ("ret", "down"): [-0.05, -0.1, -0.2],
        ("volume_ratio", "up"): [2, 3],
        ("volume_ratio", None): [2, 3],
    }
    values = table.get((name, direction), [])
    if name == "ret":
        options = [(f"{v * 100:+g}%", v) for v in values]
    elif name == "volume_ratio":
        options = [(f"평소의 {v}배", v) for v in values]
    else:
        options = [(f"{v}", v) for v in values]
    return f"{label}가 얼마일 때를 말씀하시나요?", options


# ── 답 반영 ──────────────────────────────────────────────────────────


def overrides_from_answers(
    questions: Sequence[Question],
    answers: Sequence[Mapping],
    *,
    read_text: Callable[[Ask, str], object] | None = None,
) -> dict:
    # answers: [{"question_id": "q1", "choice_id": "c1"} | {"question_id": "q2", "text": "20일선 정도"}]
    by_id = {q.id: q for q in questions}
    out: dict = {}
    for answer in answers:
        question = by_id.get(answer.get("question_id"))
        if question is None:
            continue
        if answer.get("choice_id"):
            choice = next((c for c in question.choices if c.id == answer["choice_id"]), None)
            if choice is not None:
                out[question.ask.key] = choice.value
        elif answer.get("text") and read_text is not None:
            value = read_text(question.ask, answer["text"])
            if value is not None:
                out[question.ask.key] = value
    return out


def read_answer_text(ask: Ask, text: str, find: Callable[[str], SpanResult]) -> object | None:
    # 직접 입력한 답에서 그 빈칸의 값만 꺼낸다. 조각 찾기만 쓰고 AI 는 부르지 않는다.
    # 꺼내지 못하면 None 이다. 그 빈칸은 다음 차례에 다시 묻는다
    spans = find(text).spans
    numbers = [s for s in spans if s.kind == "number" and isinstance(s.meaning.get("value"), (int, float))]
    windows = [s.meaning["window"] for s in spans if s.kind == "indicator" and s.meaning.get("window")]
    kind = ask.kind
    if kind == "universe" or (kind == "ambiguous_quote" and ask.context.get("slot") == "universe"):
        tickers = [s.meaning["ticker"] for s in spans if s.kind == "instrument"]
        return tickers or None
    if kind in ("ma_window", "window_not_allowed"):
        found = windows or [s.meaning["value"] for s in numbers]
        return int(found[0]) if found else None
    if kind == "cross_windows":
        found = sorted(windows or [int(s.meaning["value"]) for s in numbers])
        return tuple(found[:2]) if len(found) >= 2 else None
    if kind == "threshold":
        return number_value(numbers[0], ask.context.get("indicator", "")) if numbers else None
    if kind == "direction":
        ops = [s.meaning["op"] for s in spans if s.kind == "verb" and s.meaning.get("op")]
        return ops[0] if ops else None
    return None
