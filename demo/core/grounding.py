"""4단계 근거 검사: "말한 대로 적혔나" (docs/strategy-spec-v0.2.md "4단계. 근거 검사"). 규칙 코드, LLM 미사용.

입력: 전략서(검사 대상 값) + Grounding(원문, 되묻기 답, 값마다 LLM 이 적은 근거와 출처).
값은 전략서에서 읽는다. 그래서 조립 뒤에 누가 값을 바꿨어도(테스트의 '일부러 틀린 전략서') 잡힌다.

  지어낸 값     모든 값의 근거가 원문(+되묻기 답)에 글자 그대로 있는가 (EVIDENCE_NOT_IN_TEXT)
                근거를 표현 표로 다시 바꿨을 때 같은 값이 나오는가 (VALUE_MISMATCH: "5일선" 근거인데 20일)
                근거 없는 값은 기본값 표에서 왔는가 (NO_EVIDENCE)
                연산자를 정한 동사·조건 구절이 근거에 없으면 LLM 값이라 확인을 받는다 (UNGROUNDED_OP)
  빠뜨린 내용   원문에서 의미 있는 낱말(종목 별칭, 나라, 지수, 지표·조건 구절, 사고파는 동사)을 뽑아
                모두 어떤 빈칸의 근거로 쓰였는가 (OMITTED)
  숫자          원문의 숫자가 전략서에 다 있는가 (NUMBER_MISSING)

낱말 목록 = data/expressions.json (indicator_aliases · of_aliases · phrases · meaningful_words) + data/tickers.json 별칭.
목록에 없는 낱말은 검사하지 않는다. 목록의 범위가 곧 이 검사가 잡을 수 있는 범위다.

되묻기 답으로 정해진 값(출처 answer)·'조건이 반대가 되면'으로 만든 값(negate)은 규칙이 적용한 것이라
표현 표 대조를 하지 않고, 답이 실제로 있었는지만 본다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from core.decide import _OF_INDEX, _norm, _parse_money, op_from_table, phrase_condition, with_next_word
from core.dictionary import INDICATORS, indicator_label, load_expressions, load_tickers
from core.schema import Compare, Operand, Strategy, Value, iter_compares

_OP_WORD = {"<=": "이하", "<": "아래", ">=": "이상", ">": "위", "cross_below": "아래로 내려가는 순간",
            "cross_above": "위로 올라가는 순간"}
IGNORE = "무시하고 진행"
YES = "네"
RESTATE = "문장을 고쳐서 다시 입력"


@dataclass
class GroundItem:
    path: str                    # assets[0] | max_weight | rebalance | backtest.period | backtest.initial_cash |
    #                              backtest.costs | rules.entry(.all[0]) …
    evidence: str                # LLM 이 적은 원문 근거 (출처 answer 면 답 이전의 원래 근거)
    source: str = "text"         # text | answer | negate
    answer: str = ""             # source == answer 일 때 그 답
    op_source: str = ""          # 비교: phrase | direction | llm | negate
    defaults: list[str] = field(default_factory=list)   # 비교 안 기본값 자리 ("left.window", "" = 비교 전체)
    extra: str = ""              # 종목 비중 근거 (종목 칸 전체 근거 + 종목 근거)


@dataclass
class Grounding:
    text: str
    answers: list[str] = field(default_factory=list)
    items: list[GroundItem] = field(default_factory=list)
    handled: list[str] = field(default_factory=list)     # 전략서 값은 아니지만 처리한 근거 (배당, 빼고 진행)
    hold: bool = False                                   # '계속 갖고 있기' 답 (팔 조건 없음이 정상)


@dataclass
class Finding:
    field: str
    code: str
    message: str                 # 무엇이 걸렸는지 (LLM 다시 채우기에도 그대로 보낸다)
    question: str
    options: list[str]


# ---------------------------------------------------------------- 표현 표 읽기

def _ex() -> dict:
    return load_expressions()


def _num(s: str) -> list[float]:
    return [float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*(?:\.\d+)?", s)]


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-6 * max(1.0, abs(a), abs(b))


def _in(xs, v: float) -> bool:
    return any(_close(x, v) for x in xs)


def ticker_spans(text: str) -> list[tuple[int, int, str]]:
    """원문 속 종목 별칭 (가장 긴 것 우선, 겹치지 않게). (시작, 끝, 종목코드)."""
    cands = []
    for t in load_tickers().values():
        for a in {t.name, *t.aliases}:
            for m in re.finditer(re.escape(a), text):
                cands.append((m.start(), m.end(), t.ticker))
    cands.sort(key=lambda x: (-(x[1] - x[0]), x[0]))
    taken: list[tuple[int, int, str]] = []
    for c in cands:
        if all(c[1] <= s or c[0] >= e for s, e, _ in taken):
            taken.append(c)
    return sorted(taken)


def _mask(text: str, spans) -> str:
    out = list(text)
    for s, e, *_ in spans:
        out[s:e] = [" "] * (e - s)
    return "".join(out)


def _of_names(of: str) -> list[str]:
    names = [p for a in _ex()["of_aliases"] if a["of"] == of for p in a["patterns"]]
    names += list(_OF_INDEX.get(of, ()))
    t = load_tickers().get(of)
    if t:
        names += [t.name, *t.aliases]
    return [n for n in names if n and n.upper() != "SELF"]


def _has_word(ev: str, words) -> bool:
    n = _norm(ev)
    return any(_norm(w) and _norm(w) in n for w in words)


def mentions(ev: str, context: str = "") -> set[tuple[str, Optional[int]]]:
    """근거 → 표현 표가 가리키는 (지표, 기간) 들. context: 조건 구절을 찾을 때 쓰는 넓힌 근거."""
    out: set[tuple[str, Optional[int]]] = set()
    for a in _ex()["indicator_aliases"]:
        if any(p in ev for p in a["patterns"]):
            out.add((a["indicator"], a.get("window")))
    for rx, ind in ((r"(\d+)\s*일\s*(?:선|이평|이동평균)", "ma"), (r"(\d+)\s*일\s*RSI", "rsi"), (r"RSI\s*\((\d+)", "rsi"),
                    (r"(\d+)\s*일\s*수익률", "ret"), (r"(\d+)\s*일\s*변동성", "vol")):
        for m in re.finditer(rx, ev, flags=re.I):
            out.add((ind, int(m.group(1))))
    if re.search(r"\d+\s*(?:거래일|일\s*(?:보유|지나|뒤|후))", ev):
        out.add(("holding_days", None))
    pc = phrase_condition(context or ev)
    if pc:
        for side in ("left", "right"):
            o = pc[1][side]
            if "indicator" in o:
                out.add((o["indicator"], o.get("window")))
    return out


def _operand_dict(o) -> dict:
    if isinstance(o, Value):
        return {"value": o.value}
    d = {"indicator": o.indicator, "of": o.of}
    if o.window is not None:
        d["window"] = o.window
    return d


def _phrase_same(c: Compare, cond: dict) -> bool:
    def same(o, p):
        if "value" in p:
            return isinstance(o, Value) and _close(o.value, float(p["value"]))
        return (isinstance(o, Operand) and o.indicator == p["indicator"] and o.of == p.get("of", "self")
                and o.window == p.get("window"))
    return c.op == cond["op"] and same(c.left, cond["left"]) and same(c.right, cond["right"])


def _clause(c: Compare) -> str:
    def name(o):
        if isinstance(o, Value):
            return f"{o.value:g}"
        who = "" if o.of in ("self", "VIX") else f"{o.of} "
        return who + indicator_label(o.indicator, o.window)
    return f"{name(c.left)} {name(c.right)} {_OP_WORD[c.op]}"


def _jo(word: str, a: str, b: str) -> str:
    from core.decide import _j
    return _j(word, a, b)


# ---------------------------------------------------------------- 지어낸 값

def _fabricated(f: list[Finding], path: str, ev: str, msg: str) -> None:
    f.append(Finding(path, "VALUE_MISMATCH", msg,
                     f"{_jo(repr(ev), '을', '를') if ev else '이 값을'} 전략서에 어떻게 적을지 확인이 필요해요. 문장을 고쳐 주시겠어요?",
                     [RESTATE]))


def _check_compare(c: Compare, it: GroundItem, f: list[Finding], text: str = "") -> None:
    ev, path = it.evidence, it.path
    ctx = with_next_word(ev, text)
    pc = phrase_condition(ctx)
    if pc and _phrase_same(c, pc[1]):
        return                                  # 표현 표 구절(과매도 = RSI 30, "5일선 깨지면" 등)이 비교 전체를 정함
    if "" in it.defaults:
        _fabricated(f, path, ev, f"{path}: 기본값 구절 {ev!r}가 정하는 비교와 전략서의 비교({_clause(c)})가 다릅니다.")
        return
    ms = mentions(ev, ctx)
    inds = {i for i, _ in ms}
    operands = [("left", c.left)] + ([("right", c.right)] if isinstance(c.right, Operand) else [])
    price_pair = isinstance(c.right, Operand) and {c.left.indicator, c.right.indicator} <= {"close", "ma"}
    for where, o in operands:
        w_default = f"{where}.window" in it.defaults
        single = len(INDICATORS[o.indicator]["windows"]) == 1
        ok = ((o.indicator, o.window) in ms or (o.indicator in inds and (single or w_default))
              or (o.indicator == "close" and price_pair))          # "5일선 깨지면": 종가는 말하지 않아도 종가
        if not ok:
            said = [indicator_label(i, w) for i, w in sorted(ms, key=str) if w is not None or
                    INDICATORS[i]["windows"] == [None]]
            _fabricated(f, f"{path}.{where}", ev,
                        f"{path}.{where}: 근거 {ev!r}에는 {indicator_label(o.indicator, o.window)}에 해당하는 말이 없습니다"
                        + (f" (근거를 표현 표로 바꾸면: {', '.join(said)})" if said else "") + ".")
        if o.of != "self" and not _has_word(ev, _of_names(o.of)):
            _fabricated(f, f"{path}.{where}.of", ev, f"{path}.{where}.of: 근거 {ev!r}에 '{o.of}'를 가리키는 말이 없습니다.")
    for a in _ex()["of_aliases"]:
        if any(p in ev for p in a["patterns"]) and all(o.of != a["of"] for _, o in operands):
            _fabricated(f, f"{path}.left.of", ev,
                        f"{path}: 근거 {ev!r}는 {a['of']}의 값을 말하는데 전략서에는 {a['of']}의 값이 없습니다.")
    if isinstance(c.right, Value) and "right.value" not in it.defaults:
        v = c.right.value
        if not _in(_num(ev), abs(v)):
            _fabricated(f, f"{path}.right.value", ev,
                        f"{path}.right.value: 근거 {ev!r}에 숫자 {v:g}가 없습니다 (근거의 숫자: "
                        f"{', '.join(f'{x:g}' for x in _num(ev)) or '없음'}).")
    table_op, how = op_from_table(ev, ctx)
    if table_op is not None and table_op != c.op:
        _fabricated(f, f"{path}.op", ev, f"{path}.op: 근거 {ev!r}의 구절은 '{_OP_WORD[table_op]}'({table_op})인데 "
                                         f"전략서에는 {c.op} 입니다.")
    elif table_op is None:
        why = "동사 구절이 서로 다른 연산자를 가리킵니다" if how == "conflict" else "표현 표의 동사·조건 구절이 없습니다"
        f.append(Finding(f"{path}.op", "UNGROUNDED_OP",
                         f"{path}.op: 근거 {ev!r}에 {why}. 연산자 {c.op} 는 AI 가 정한 값입니다.",
                         f"{_jo(repr(ev), '을', '를')} '{_clause(c)}'로 볼까요?", [YES, RESTATE]))


def _check_scalar(spec: Strategy, it: GroundItem, f: list[Finding]) -> None:
    ev, path = it.evidence, it.path
    bt = spec.backtest
    if path == "max_weight" and spec.max_weight is not None and not _in(_num(ev), spec.max_weight * 100):
        _fabricated(f, path, ev, f"max_weight: 근거 {ev!r}에 {spec.max_weight * 100:g}%가 없습니다.")
    elif path == "rebalance" and spec.rebalance is not None:
        found = {a["rebalance"] for a in _ex()["rebalance_aliases"] if any(p in ev for p in a["patterns"])}
        if spec.rebalance not in found:
            _fabricated(f, path, ev, f"rebalance: 근거 {ev!r}를 표현 표로 바꾸면 "
                                     f"{', '.join(sorted(found)) or '해당 주기 없음'} 인데 전략서에는 {spec.rebalance} 입니다.")
    elif path == "backtest.period":
        years = {int(y) for y in re.findall(r"(?<!\d)(\d{4})(?!\d)", ev)}
        for k, d in (("start", bt.start), ("end", bt.end)):
            if f"backtest.{k}" not in spec.defaults_applied and int(d[:4]) not in years:
                _fabricated(f, f"backtest.{k}", ev, f"backtest.{k}: 근거 {ev!r}에 {d[:4]}년이 없습니다.")
    elif path == "backtest.initial_cash":
        v = _parse_money(ev)
        if v is None or not _close(v, bt.initial_cash):
            _fabricated(f, path, ev, f"backtest.initial_cash: 근거 {ev!r}를 금액으로 읽으면 "
                                     f"{'읽을 수 없음' if v is None else f'{v:,.0f}원'}인데 전략서에는 {bt.initial_cash:,.0f}원입니다.")
    elif path == "backtest.costs":
        for k, v in (("commission_rate", bt.commission_rate), ("slippage_rate", bt.slippage_rate)):
            if f"backtest.{k}" not in spec.defaults_applied and not _in(_num(ev), v * 100):
                _fabricated(f, f"backtest.{k}", ev, f"backtest.{k}: 근거 {ev!r}에 {v * 100:g}%가 없습니다.")


def _check_asset(spec: Strategy, i: int, it: GroundItem, f: list[Finding]) -> None:
    a = spec.assets[i]
    ev = it.evidence
    found = {t for _, _, t in ticker_spans(ev)}
    if a.ticker not in found:
        names = ", ".join(load_tickers()[t].name for t in sorted(found)) or "목록의 종목 없음"
        _fabricated(f, f"assets[{i}].ticker", ev,
                    f"assets[{i}]: 근거 {ev!r}가 가리키는 종목은 {names} 인데 전략서에는 {a.name}({a.ticker}) 입니다.")
    if a.weight is not None and "assets.weight" not in spec.defaults_applied:
        we = it.extra or ev
        pct = a.weight * 100
        ratio = [float(x) for m in re.finditer(r"(\d+)\s*(?::|대)\s*(\d+)", we) for x in m.groups()]
        if not (_in(_num(we), pct) or ("반반" in we and _close(a.weight, 0.5)) or _in(ratio, pct)):
            _fabricated(f, f"assets[{i}].weight", we, f"assets[{i}].weight: 근거 {we!r}에 비중 {pct:g}%가 없습니다.")


def _value_paths(spec: Strategy) -> list[str]:
    """근거가 있어야 하는 전략서 값의 경로. 기본값으로 채운 것은 뺀다."""
    d = set(spec.defaults_applied)
    out = [f"assets[{i}]" for i in range(len(spec.assets))]
    if spec.max_weight is not None:
        out.append("max_weight")
    if spec.rebalance is not None and "rebalance" not in d:
        out.append("rebalance")
    if spec.rules is not None:
        # 비교 전체가 기본값 구절("과매도" = RSI 30)이어도 근거가 있으니 검사한다 (_check_compare 가 구절과 대조)
        out += [p for p, _ in iter_compares(spec.rules.entry, "rules.entry")]
        out += [p for p, _ in iter_compares(spec.rules.exit, "rules.exit")]
    if "backtest.period" not in d and not {"backtest.start", "backtest.end"} <= d:
        out.append("backtest.period")
    if "backtest.initial_cash" not in d:
        out.append("backtest.initial_cash")
    if "backtest.costs" not in d and not {"backtest.commission_rate", "backtest.slippage_rate"} <= d:
        out.append("backtest.costs")
    return out


def _fabrication(spec: Strategy, g: Grounding) -> list[Finding]:
    f: list[Finding] = []
    items = {it.path: it for it in g.items}
    compares = {}
    if spec.rules is not None:
        compares = dict([*iter_compares(spec.rules.entry, "rules.entry"), *iter_compares(spec.rules.exit, "rules.exit")])
    text_n, answers_n = _norm(g.text), [_norm(a) for a in g.answers]
    for path in _value_paths(spec):
        it = items.get(path)
        if it is None or (it.source == "text" and not it.evidence.strip()):
            f.append(Finding(path, "NO_EVIDENCE", f"{path}: 근거가 없는데 기본값 표의 값도 아닙니다.",
                             "이 값이 문장의 어디에서 왔는지 확인이 필요해요. 문장을 고쳐 주시겠어요?", [RESTATE]))
            continue
        if it.source == "negate":
            continue
        if it.source == "answer":
            if _norm(it.answer) not in answers_n:
                f.append(Finding(path, "EVIDENCE_NOT_IN_TEXT", f"{path}: 근거로 적힌 답 {_jo(repr(it.answer), '이', '가')} 되묻기 답에 없습니다.",
                                 "이 값을 확인할 수 없어요. 문장을 고쳐 주시겠어요?", [RESTATE]))
            continue
        if _norm(it.evidence) not in text_n:
            f.append(Finding(path, "EVIDENCE_NOT_IN_TEXT", f"{path}: 근거 {it.evidence!r}가 원문에 글자 그대로 없습니다.",
                             f"{_jo(repr(it.evidence), '은', '는')} 문장에 없는 말이에요. 문장을 고쳐 주시겠어요?", [RESTATE]))
            continue
        if path.startswith("assets["):
            _check_asset(spec, int(path[7:-1]), it, f)
        elif path in compares:
            _check_compare(compares[path], it, f, g.text)
        else:
            _check_scalar(spec, it, f)
    return f


# ---------------------------------------------------------------- 빠뜨린 내용 · 숫자

def _used_evidence(g: Grounding) -> list[str]:
    out = [it.evidence for it in g.items] + [it.extra for it in g.items if it.extra]
    out += [it.answer for it in g.items if it.answer] + list(g.handled)
    return [e for e in out if e]


def _omission(spec: Strategy, g: Grounding) -> list[Finding]:
    f: list[Finding] = []
    text = g.text
    spans = ticker_spans(text)
    in_spec = {a.ticker for a in spec.assets}
    if spec.rules is not None:
        for _, c in [*iter_compares(spec.rules.entry, ""), *iter_compares(spec.rules.exit, "")]:
            for o in (c.left, c.right):
                if isinstance(o, Operand):
                    in_spec.add(o.of)
    used = _used_evidence(g)
    # 종목은 근거에 들어 있는 것만으로는 부족하고 전략서에 있어야 한다 ('빼고 진행'으로 처리한 것은 예외)
    handled_tickers = {tt for e in g.handled for _, _, tt in ticker_spans(e)}
    for _, _, t in spans:
        if t not in in_spec and t not in handled_tickers:
            name = load_tickers()[t].name
            f.append(Finding("assets", "OMITTED", f"원문의 종목 {_jo(repr(name), '이', '가')} 전략서에 없습니다.",
                             f"{_jo(repr(name), '을', '를')} 어떻게 반영할까요?", [IGNORE, RESTATE]))
    rest = _mask(text, spans)
    ex = _ex()
    words: list[str] = list(ex["meaningful_words"]["country"])
    words += [p for a in ex["of_aliases"] for p in a["patterns"]]
    words += [p for a in ex["indicator_aliases"] for p in a["patterns"]]
    found: list[str] = []
    for w in dict.fromkeys(words):
        if w in rest and not any(w in x and x != w and x in rest for x in found + words):   # 더 긴 낱말에 포함되면 그쪽만
            found.append(w)
    # 조건 구절 중 숫자 자리가 없는 것(과매도·과매수)과 "{n}일선" (숫자 자리가 있는 구절은 근거에 동사까지
    # 들어가는지가 LLM 마다 달라 낱말 단위로만 본다)
    found += [p for _rx, p, _ in _phrase_regexes() if "{" not in p and p in rest]
    found += [m.group(0) for m in re.finditer(r"\d+\s*일\s*(?:선|이평|이동평균)", rest)]
    for w in dict.fromkeys(found):
        if not any(_norm(w) in _norm(e) for e in used):
            f.append(Finding("(원문)", "OMITTED", f"원문의 {_jo(repr(w), '이', '가')} 어느 빈칸의 근거로도 쓰이지 않았습니다.",
                             f"{_jo(repr(w), '을', '를')} 어떻게 반영할까요?", [IGNORE, RESTATE]))
    mw = ex["meaningful_words"]
    rules = spec.rules
    if any(w in rest for w in mw["sell"]) and (rules is None or (rules.exit is None and not g.hold)):
        w = next(w for w in mw["sell"] if w in rest)
        f.append(Finding("rules.exit", "OMITTED", f"원문에 파는 말({w!r})이 있는데 팔 때 조건이 없습니다.",
                         f"{_jo(repr(w), '을', '를')} 어떻게 반영할까요?", [IGNORE, RESTATE]))
    if any(w in rest for w in mw["buy"]) and rules is not None and rules.entry is None:
        w = next(w for w in mw["buy"] if w in rest)
        f.append(Finding("rules.entry", "OMITTED", f"원문에 사는 말({w!r})이 있는데 살 때 조건이 없습니다.",
                         f"{_jo(repr(w), '을', '를')} 어떻게 반영할까요?", [IGNORE, RESTATE]))
    return f


def _phrase_regexes():
    from core.decide import _condition_phrases
    return _condition_phrases()


def spec_numbers(spec: Strategy) -> list[float]:
    xs: list[float] = []
    for a in spec.assets:
        if a.weight is not None:
            xs.append(a.weight * 100)
    if spec.max_weight is not None:
        xs.append(spec.max_weight * 100)
    if spec.rules is not None:
        for _, c in [*iter_compares(spec.rules.entry, ""), *iter_compares(spec.rules.exit, "")]:
            for o in (c.left, c.right):
                if isinstance(o, Value):
                    xs += [o.value, abs(o.value)]
                elif o.window is not None:
                    xs.append(float(o.window))
    bt = spec.backtest
    xs += [float(bt.start[:4]), float(bt.end[:4]), bt.initial_cash, bt.commission_rate * 100, bt.slippage_rate * 100]
    return xs


_MONEY = re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:억|천만|백만|만)(?:\s*\d[\d,]*(?:\.\d+)?\s*(?:억|천만|백만|만))*\s*원?")


def _numbers_check(spec: Strategy, g: Grounding) -> list[Finding]:
    """원문의 숫자가 전략서에 다 있는가. 종목·지수 이름 속 숫자(KODEX 200, 코스닥150)와
    되묻기 답으로 바뀐 근거·빼고 진행한 근거 속 숫자는 보지 않는다."""
    f: list[Finding] = []
    text = _mask(g.text, ticker_spans(g.text))
    for a in _ex()["of_aliases"]:
        for p in a["patterns"]:
            if re.search(r"\d", p):
                text = text.replace(p, " " * len(p))
    for e in [it.evidence for it in g.items if it.source == "answer"] + list(g.handled):
        if e:
            text = text.replace(e, " " * len(e))
    have = spec_numbers(spec)
    for m in list(_MONEY.finditer(text)):
        v = _parse_money(m.group(0))
        if v is not None and not _in(have, v):
            f.append(Finding("(원문)", "NUMBER_MISSING", f"원문의 금액 {m.group(0).strip()!r}({v:,.0f}원)이 전략서에 없습니다.",
                             f"문장의 {_jo(m.group(0).strip(), '이', '가')} 전략서에 없어요. 어떻게 할까요?", [IGNORE, RESTATE]))
        text = text[:m.start()] + " " * (m.end() - m.start()) + text[m.end():]
    for m in re.finditer(r"\d[\d,]*(?:\.\d+)?", text):
        v = float(m.group(0).replace(",", ""))
        if not _in(have, v):
            f.append(Finding("(원문)", "NUMBER_MISSING", f"원문의 숫자 {_jo(m.group(0), '이', '가')} 전략서에 없습니다.",
                             f"문장의 숫자 {_jo(m.group(0), '이', '가')} 전략서에 없어요. 어떻게 할까요?", [IGNORE, RESTATE]))
    return f


def check(spec: Strategy, g: Grounding) -> list[Finding]:
    """4단계 근거 검사. 같은 내용은 한 번만."""
    out = _fabrication(spec, g) + _omission(spec, g) + _numbers_check(spec, g)
    seen, uniq = set(), []
    for x in out:
        k = (x.code, x.message)
        if k not in seen:
            seen.add(k)
            uniq.append(x)
    return uniq
