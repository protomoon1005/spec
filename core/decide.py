"""빈칸 상태 → 판정 (docs/strategy-spec-v0.2.md "사용자가 말하지 않은 빈칸 채우기", "거절하는 경우", "판단하는 순서").
규칙 코드다. LLM 을 쓰지 않는다.

판단하는 순서
  1. 거절할 거리가 하나라도 있으면 거절 (사유 코드 + 문장 속 근거). 거절이 되묻기보다 먼저다.
  2. 애매함 → 되묻기. 말 안 함 → 기본값 표대로 기본값 또는 되묻기.
  3. 되묻기는 한 번에 최대 3개, 최대 2번. 2번 물어도 남으면 UNRESOLVED 거절.
  4. 남은 게 없으면 진행 (core.assemble 이 전략서를 조립한다).

LLM 의 상태는 출발점일 뿐이고, 값은 규칙이 사전과 다시 대조한다.
  - LLM 이 filled 라고 해도 사전에 없는 기간(7일선)·지표·종목이면 규칙이 잡는다.
  - 못 다룸(unsupported)이라도 v0.2·데모 범위가 되묻기로 정한 것은 되묻는다:
      없는 기간("7일선") → 가까운 기간을 선택지로 / 종류로 말한 종목 → 이름을 / 목록에 없는 한국 종목 → 다른 종목을
  - 사전으로 확인할 수 없는 못 다룸(미래 참조·장중·매매 방식·투자와 무관·해외/기타 상품·없는 지표)은
    LLM 이 적은 분류(kind) 또는 표현 표(data/expressions.json)의 거절 구절로 사유 코드를 정한다. 구절이 우선.
  - 상태가 애매함·못 다룸인데 규칙이 보기엔 값이 다 맞으면, 그대로 쓰지 않고 "이렇게 볼까요?"로 확인한다.

답 적용
  answers = [1번째 되묻기의 답, 2번째 되묻기의 답], 각 답은 {질문 id: 답}.
  같은 빈칸 상태에서 같은 질문이 나오므로, 라운드마다 질문을 다시 계산해 그 라운드에 물은 질문의 답만 적용한다.
  답이 선택지와 같으면 그 선택지의 값을 넣고, 아니면 직접 입력으로 보고 규칙으로 읽는다(종목 이름, 숫자).
  규칙으로 읽을 수 없는 답은 적용하지 않는다 → 빈칸이 남아 다음 라운드에 다시 묻는다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable, Optional

from core.dictionary import INDICATORS, REBALANCE, REJECT_CODES, TickerInfo, load_expressions, load_tickers
from core.slot_fill import AssetItem, Comparison, CondSlot, Operand, RightSide, SlotFill

MAX_QUESTIONS = 3
MAX_ROUNDS = 2
DIRECT = "직접 입력"
RESTATE = "문장을 고쳐서 다시 입력"

# v0.2 기본값 표 중 값이 있는 것 (나머지 행은 규칙 안에 있다)
DEFAULTS = {
    "rsi_window": 14,
    "rebalance_allocation": "monthly_first",   # 조건 없는 전략. 조건 있는 전략은 없음(None)
    "start": "2023-01-01",
    "end": "2025-12-31",
    "initial_cash": 10_000_000,
    "commission_rate": 0.00015,
    "slippage_rate": 0.0005,
}
DIVIDEND_WORDS = ("배당", "고배당", "분배금", "월배당")

# 못 다룸 분류(kind) → 거절 사유 코드. 여기 없는 kind(not_in_list·category·bad_window·other)는 되묻기 쪽.
KIND_TO_CODE = {
    "future_info": "FUTURE_REF",
    "unknown_indicator": "UNKNOWN_INDICATOR",
    "foreign_asset": "UNSUPPORTED_ASSET",
    "other_product": "UNSUPPORTED_ASSET",
    "trade_method": "UNSUPPORTED_ACTION",
    "intraday": "INTRADAY",
    "not_investment": "OUT_OF_DOMAIN",
}

_LOW = {"<", "<=", "cross_below"}
_NEGATE = {"<": ">=", "<=": ">", ">": "<=", ">=": "<", "cross_below": "cross_above", "cross_above": "cross_below"}
_OP_WORD = {"<=": "이하", "<": "아래", ">=": "이상", ">": "위", "cross_below": "아래로 내려가는 순간",
            "cross_above": "위로 올라가는 순간"}


# ---------------------------------------------------------------- 결과 타입

@dataclass
class Question:
    id: str
    slot: str
    text: str
    options: list[str]
    evidence: str = ""
    hint: Optional[str] = None
    apply: Optional[Callable[["State", str], None]] = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict:
        return {"id": self.id, "slot": self.slot, "text": self.text, "options": self.options,
                "evidence": self.evidence, "hint": self.hint}


@dataclass
class Rejection:
    code: str
    detail: str
    evidence: str = ""
    slot: str = ""

    @property
    def label(self) -> str:
        return REJECT_CODES[self.code]

    def to_dict(self) -> dict:
        return {"code": self.code, "label": self.label, "detail": self.detail, "evidence": self.evidence,
                "slot": self.slot}


@dataclass
class State:
    """답을 적용해 가며 바뀌는 빈칸 상태."""
    slots: SlotFill
    confirmed: set[str] = field(default_factory=set)    # "이렇게 볼까요?"에 네 / 답으로 채운 항목
    dropped: set[str] = field(default_factory=set)      # "빼고 진행"
    derived: dict[str, str] = field(default_factory=dict)   # {"exit": "negate"} 조건이 반대가 되면
    hold: bool = False                                   # 언제 팔까요 → 계속 갖고 있기
    start_holding: bool = False                          # 언제 살까요 → 처음부터 갖고 시작
    answered: list[dict] = field(default_factory=list)


@dataclass
class ResolvedCompare:
    left: dict
    op: str
    right: dict
    evidence: str
    defaults: list[str] = field(default_factory=list)   # 비교 안에서 기본값을 쓴 자리: "left.window", "right.value", ""(전체)


@dataclass
class ResolvedCond:
    logic: str
    items: list[ResolvedCompare]
    source: str = "text"                    # text | negate (조건이 반대가 되면)


@dataclass
class Resolved:
    assets: list[dict] = field(default_factory=list)     # {ticker, name, weight(0~1|None), evidence}
    max_weight: Optional[float] = None
    rebalance: Optional[str] = None
    entry: Optional[ResolvedCond] = None
    exit: Optional[ResolvedCond] = None
    hold: bool = False
    start: str = DEFAULTS["start"]
    end: str = DEFAULTS["end"]
    initial_cash: float = DEFAULTS["initial_cash"]
    commission_rate: float = DEFAULTS["commission_rate"]
    slippage_rate: float = DEFAULTS["slippage_rate"]
    dividend_mentioned: bool = False
    defaults: list[str] = field(default_factory=list)    # 빈칸 단위 기본값: assets.weight, rebalance, backtest.*


@dataclass
class Decision:
    verdict: str                              # proceed | clarify | reject
    state: State
    questions: list[Question] = field(default_factory=list)    # 이번에 물을 것 (최대 3개)
    pending: list[Question] = field(default_factory=list)      # 남은 질문 전체
    rejection: Optional[Rejection] = None
    rejections: list[Rejection] = field(default_factory=list)  # 찾은 거절 사유 전부 (첫 번째가 rejection)
    round: int = 0                            # clarify 면 몇 번째 되묻기인지 (1|2)
    resolved: Optional[Resolved] = None


# ---------------------------------------------------------------- 한국어 헬퍼

_DIGIT_BATCHIM = {"0": True, "1": True, "2": False, "3": True, "4": False, "5": False, "6": True, "7": True,
                  "8": True, "9": False}


def _batchim(word: str) -> bool:
    w = re.sub(r"[\s')\]]+$", "", word)
    if not w:
        return False
    ch = w[-1]
    if "가" <= ch <= "힣":
        return (ord(ch) - 0xAC00) % 28 != 0
    if ch in _DIGIT_BATCHIM:
        return _DIGIT_BATCHIM[ch]
    return ch.upper() in "LMNR"


def _j(word: str, a: str, b: str) -> str:
    """받침이 있으면 a, 없으면 b. ('은','는') ('과','와') ('으로','로') ('을','를')"""
    if a == "으로":
        w = re.sub(r"[\s')\]]+$", "", word)
        if w and "가" <= w[-1] <= "힣" and (ord(w[-1]) - 0xAC00) % 28 == 8:   # ㄹ 받침 → 로
            return word + "로"
    return word + (a if _batchim(word) else b)


def _q(word: str) -> str:
    return f"'{word}'"


def _numbers(s: str) -> list[float]:
    return [float(x.replace(",", "")) for x in re.findall(r"-?\d[\d,]*(?:\.\d+)?", s)]


def _norm(s: Optional[str]) -> str:
    return re.sub(r"\s+", "", s or "").upper()


# ---------------------------------------------------------------- 사전 조회

@lru_cache(maxsize=None)
def _alias_index() -> dict[str, TickerInfo]:
    idx: dict[str, TickerInfo] = {}
    for t in load_tickers().values():
        for a in (t.name, t.ticker, *t.aliases):
            idx.setdefault(_norm(a), t)
    return idx


def lookup_ticker(name: Optional[str]) -> Optional[TickerInfo]:
    """종목 이름·별칭·코드가 목록과 정확히 같을 때만 찾는다 (비슷한 종목으로 바꾸지 않는다)."""
    return _alias_index().get(_norm(name)) if name else None


def ticker_names() -> str:
    return ", ".join(t.name for t in load_tickers().values())


_OF_INDEX = {
    "KOSPI200": ("KOSPI200", "코스피200", "코스피", "KOSPI", "KS200", "시장", "지수"),
    "KOSDAQ": ("KOSDAQ", "코스닥", "코스닥지수", "KQ11"),
    "VIX": ("VIX", "빅스", "공포지수"),
    "self": ("SELF", "자기", "자신", "그종목", "해당종목", "종목"),
}


def resolve_of(of: Optional[str], indicator: str) -> tuple[Optional[str], bool]:
    """(of 값, 기본값 여부). 모르는 이름이면 (None, False). 말하지 않았으면 그 종목 자신(VIX 는 VIX)."""
    if not of:
        return ("VIX" if indicator == "vix" else "self"), True
    n = _norm(of)
    for key, names in _OF_INDEX.items():
        if n in {_norm(x) for x in names}:
            return key, False
    t = lookup_ticker(of)
    return (t.ticker, False) if t else (None, False)


@lru_cache(maxsize=None)
def _indicator_alias() -> dict[str, tuple[str, Optional[int]]]:
    m: dict[str, tuple[str, Optional[int]]] = {}
    for a in load_expressions()["indicator_aliases"]:
        for p in a["patterns"]:
            m.setdefault(_norm(p), (a["indicator"], a.get("window")))
    for k in INDICATORS:
        m[_norm(k)] = (k, None)
    m.update({_norm(x): ("ma", None) for x in ("sma", "이동평균", "이동평균선", "이평", "이평선")})
    return m


def resolve_indicator(name: Optional[str]) -> tuple[Optional[str], Optional[int]]:
    """(지표 id, 이름에 들어 있던 기간). 사전에 없으면 (None, None)."""
    if not name:
        return None, None
    return _indicator_alias().get(_norm(name), (None, None))


def _phrases(verdict: str) -> list[dict]:
    return [p for p in load_expressions()["phrases"] if p["verdict"] == verdict and p["patterns"]]


def reject_code_by_phrase(text: str) -> Optional[str]:
    """표현 표의 거절 구절이 근거에 있으면 그 사유 코드."""
    for p in _phrases("reject"):
        if any(pat in text for pat in p["patterns"]):
            return p["reject_code"]
    return None


def clarify_phrase(text: str) -> Optional[dict]:
    for p in _phrases("clarify"):
        if any(pat in text for pat in p["patterns"]):
            return p
    return None


def phrase_by_id(pid: str) -> dict:
    return next(p for p in load_expressions()["phrases"] if p["id"] == pid)


def split_question(q: str) -> tuple[str, list[str]]:
    """표현 표의 질문 '골든크로스를 어느 기간으로 볼까요 (5일·20일선 / 20일·60일선)?' → (본문, 선택지)."""
    m = re.match(r"^(.*?)\s*\(([^()]*)\)\s*\?$", q)
    if not m:
        return q, []
    return m.group(1).rstrip() + "?", [o.strip() for o in m.group(2).split("/")]


def op_by_direction(text: str) -> Optional[str]:
    """'~인 동안 / ~하는 순간' 표 (data/expressions.json direction_rules). 가장 긴 구절을 고른다."""
    best: tuple[int, Optional[str]] = (0, None)
    for r in load_expressions()["direction_rules"]["rules"]:
        for p in r["patterns"]:
            if p in text and len(p) > best[0]:
                best = (len(p), r["op"])
    return best[1]


def _window_label(ind: str, w: int) -> str:
    return {"ma": f"{w}일선", "rsi": f"{w}일 RSI", "ret": f"{w}일 수익률", "vol": f"{w}일 변동성",
            "volume_ratio": f"{w}일 거래량 비율"}.get(ind, f"{w}일")


def nearest_windows(ind: str, w: int) -> list[int]:
    allowed = [x for x in INDICATORS[ind]["windows"] if x is not None]
    lo = [x for x in allowed if x < w]
    hi = [x for x in allowed if x > w]
    return ([max(lo)] if lo else []) + ([min(hi)] if hi else [])


# ---------------------------------------------------------------- 답 읽기

def _pick(answer: str, options: list[str]) -> Optional[int]:
    a = answer.strip()
    return options.index(a) if a in options else None


def _parse_money(s: str) -> Optional[float]:
    t = s.replace(",", "").replace(" ", "")
    total, found = 0.0, False
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)(억|천만|백만|만|원)?", t):
        found = True
        total += float(num) * {"억": 1e8, "천만": 1e7, "백만": 1e6, "만": 1e4}.get(unit, 1)
    return total if found and total > 0 else None


def _parse_names(s: str) -> list[TickerInfo]:
    parts = [p for p in re.split(r"\s*(?:,|·|/|이랑|랑|하고|그리고)\s*", s.strip()) if p]
    found = [lookup_ticker(p) for p in parts]
    return found if found and all(found) else []


def _record(st: State, qid: str, answer: str) -> None:
    st.answered.append({"question": qid, "answer": answer})


# ---------------------------------------------------------------- 분석

@dataclass
class _Found:
    rejections: list[Rejection] = field(default_factory=list)
    questions: list[Question] = field(default_factory=list)
    resolved: Resolved = field(default_factory=Resolved)


def _items(cs: CondSlot, side: str, st: State) -> list[tuple[int, Comparison]]:
    if cs.status == "unspecified":
        return []
    return [(k, c) for k, c in enumerate(cs.value.items) if f"{side}[{k}]" not in st.dropped]


def _vague(cs: CondSlot, side: str, st: State) -> bool:
    """조건 칸에 상태는 있는데 비교를 하나도 적지 못함 ("적당한 때 팔아").
    filled 인데 비어 있는 것도 여기로 본다 — 조건이 조용히 사라지지 않게."""
    return cs.status != "unspecified" and not _items(cs, side, st)


def _has_cond(cs: CondSlot, side: str, st: State) -> bool:
    return bool(_items(cs, side, st)) or _vague(cs, side, st)


def _reject_for(status: str, kind: Optional[str], evidence: str) -> Optional[str]:
    """사전으로 확인할 수 없는 못 다룸 → 사유 코드. 구절 표가 LLM 분류보다 우선."""
    code = reject_code_by_phrase(evidence)
    if code and (status in ("unsupported", "ambiguous") or kind):
        return code
    return KIND_TO_CODE.get(kind) if kind else None


# ---- 종목

def _asset_questions(st: State, f: _Found, has_rules: bool) -> None:
    s = st.slots.assets
    items = list(enumerate(s.value))
    if not items:
        if s.status in ("ambiguous", "unsupported") and s.evidence:
            code = reject_code_by_phrase(s.evidence)
            if code:
                f.rejections.append(Rejection(code, f"{_q(s.evidence)}: {REJECT_CODES[code]}", s.evidence, "assets"))
                return
        f.questions.append(_q_no_asset(has_rules))
        return

    resolved: list[dict] = []
    for i, it in items:
        sid = f"assets[{i}]"
        t = lookup_ticker(it.said)
        if t is not None:
            if it.status in ("ambiguous", "unsupported") and sid not in st.confirmed:
                f.questions.append(_q_confirm_asset(sid, it, t))
            resolved.append({"ticker": t.ticker, "name": t.name, "weight_pct": it.weight_pct,
                             "evidence": it.evidence})
            continue
        code = _reject_for("unsupported", it.kind, f"{it.said} {it.evidence}")
        if code:
            f.rejections.append(Rejection(code, f"{_q(it.said)}: {REJECT_CODES[code]} — 한국 상장 주식·ETF 만 다룹니다."
                                          if code == "UNSUPPORTED_ASSET" else f"{_q(it.said)}: {REJECT_CODES[code]}",
                                          it.evidence, sid))
        elif it.kind == "category":
            f.questions.append(_q_asset_name(sid, it, category=True))
        else:
            f.questions.append(_q_asset_name(sid, it, category=False))
    if len(resolved) == len(items):
        _weight_questions(st, f, resolved)
        f.resolved.assets = [{"ticker": r["ticker"], "name": r["name"],
                              "weight": None if r["weight_pct"] is None else r["weight_pct"] / 100,
                              "evidence": r["evidence"]} for r in resolved]


def _q_no_asset(has_rules: bool) -> Question:
    text, opts = split_question(phrase_by_id("no_asset")["question"])
    if not has_rules:          # v0.2: KODEX 200 첫 선택지는 사고파는 조건이 있는 전략만
        opts = [DIRECT]

    def apply(st: State, ans: str) -> None:
        names = _parse_names(ans)
        if names:
            st.slots.assets.value = [AssetItem(status="filled", said=t.name, evidence="", weight_pct=None)
                                     for t in names]
            st.slots.assets.status = "filled"
            for k in range(len(names)):
                st.confirmed.add(f"assets[{k}]")
    return Question("assets:none", "assets", text, opts, hint=f"데모 종목: {ticker_names()}", apply=apply)


def _replace_asset(st: State, idx: int, names: list[TickerInfo]) -> None:
    old = st.slots.assets.value[idx]
    new = [AssetItem(status="filled", said=t.name, evidence=old.evidence,
                     weight_pct=old.weight_pct if len(names) == 1 else None) for t in names]
    st.slots.assets.value[idx:idx + 1] = new
    for k in range(idx, idx + len(new)):
        st.confirmed.add(f"assets[{k}]")


def _q_asset_name(sid: str, it: AssetItem, category: bool) -> Question:
    idx = int(sid[7:-1])
    if category:
        text = f"{_j(_q(it.said), '은', '는')} 종류로 말한 종목이에요. 데모에서는 종목 이름으로만 받아요. 어떤 종목으로 할까요?"
    else:
        text = f"{_j(_q(it.said), '은', '는')} 데모 종목 목록에 없어요. 다른 종목으로 할까요?"

    def apply(st: State, ans: str) -> None:
        names = _parse_names(ans)
        if names:
            _replace_asset(st, idx, names)
    return Question(f"{sid}:name", "assets", text, [DIRECT], it.evidence,
                    hint=f"데모 종목: {ticker_names()}", apply=apply)


def _q_confirm_asset(sid: str, it: AssetItem, t: TickerInfo) -> Question:
    idx = int(sid[7:-1])

    def apply(st: State, ans: str) -> None:
        if ans.strip() == "네":
            st.confirmed.add(sid)
            return
        names = _parse_names(ans)
        if names:
            _replace_asset(st, idx, names)
    return Question(f"{sid}:confirm", "assets", f"{_j(_q(it.evidence), '을', '를')} {_j(t.name, '으로', '로')} 볼까요?",
                    ["네", DIRECT], it.evidence, hint=it.reason or None, apply=apply)


def _weight_questions(st: State, f: _Found, resolved: list[dict]) -> None:
    ws = [r["weight_pct"] for r in resolved]
    n = len(ws)
    if all(w is None for w in ws):
        if n > 1:
            f.resolved.defaults.append("assets.weight")
        return

    def set_weights(st: State, values: list[Optional[float]]) -> None:
        for it, w in zip(st.slots.assets.value, values):
            it.weight_pct = w
        st.confirmed.add("weights")

    if any(w is None for w in ws):
        given = sum(w for w in ws if w is not None)
        opts = ["남은 비중을 똑같이 나누기", DIRECT]

        def apply(st: State, ans: str) -> None:
            if _pick(ans, opts) == 0:
                rest = (100 - given) / sum(w is None for w in ws)
                if rest > 0:
                    set_weights(st, [w if w is not None else rest for w in ws])
            elif len(nums := _numbers(ans)) == n and all(x > 0 for x in nums):
                set_weights(st, nums)
        f.questions.append(Question("assets:weights_partial", "assets",
                                    "비중을 말하지 않은 종목은 어떻게 할까요?", opts, apply=apply))
        return
    total = sum(ws)
    if any(w <= 0 or w > 100 for w in ws) or total > 100 + 1e-9:
        opts = ["똑같이 나누기", DIRECT]

        def apply(st: State, ans: str) -> None:
            if _pick(ans, opts) == 0:
                set_weights(st, [None] * n)
            elif len(nums := _numbers(ans)) == n and all(x > 0 for x in nums) and sum(nums) <= 100 + 1e-9:
                set_weights(st, nums)
        f.questions.append(Question("assets:weights_sum", "assets", f"비중 합이 {total:g}%예요. 어떻게 나눌까요?",
                                    opts, apply=apply))


def _max_weight(st: State, f: _Found) -> None:
    s = st.slots.max_weight
    if s.status == "unspecified" or "max_weight" in st.dropped:
        return
    if s.status == "filled" and s.value is not None and 0 < s.value <= 100:
        f.resolved.max_weight = s.value / 100
    elif "max_weight" not in st.confirmed:
        def apply(st: State, ans: str) -> None:
            nums = _numbers(ans)
            if len(nums) == 1 and 0 < nums[0] <= 100:
                st.slots.max_weight.value, st.slots.max_weight.status = nums[0], "filled"
        f.questions.append(Question("max_weight:value", "max_weight", "종목당 최대 비중을 몇 %로 할까요?", [DIRECT],
                                    s.evidence, apply=apply))
        return
    for i, a in enumerate(f.resolved.assets):
        if a["weight"] is not None and f.resolved.max_weight is not None and a["weight"] > f.resolved.max_weight + 1e-9:
            w, m = a["weight"] * 100, f.resolved.max_weight * 100
            opts = [f"{w:g}% 담기", f"{m:g}%에 맞추기"]

            def apply(st: State, ans: str, i=i, m=m) -> None:
                k = _pick(ans, opts)
                if k == 0:
                    st.dropped.add("max_weight")
                elif k == 1:
                    st.slots.assets.value[i].weight_pct = m
            f.questions.append(Question(f"assets[{i}]:over_max", "max_weight",
                                        f"{_j(a['name'], '을', '를')} {w:g}% 담을까요, 최대 비중 {m:g}%에 맞출까요?",
                                        opts, s.evidence, apply=apply))


# ---- 리밸런싱

_PERIOD_DAYS = {"weekly": 7, "monthly_first": 30, "monthly_mid": 30, "quarterly": 91}


def _rebalance(st: State, f: _Found, has_rules: bool) -> None:
    s = st.slots.rebalance
    if s.status == "unspecified":
        if not has_rules:
            f.resolved.rebalance = DEFAULTS["rebalance_allocation"]
            f.resolved.defaults.append("rebalance")
        return
    if s.status == "filled" and s.value in REBALANCE:
        f.resolved.rebalance = s.value
        return
    if s.value in REBALANCE and "rebalance" in st.confirmed:
        f.resolved.rebalance = s.value
        return
    ev = s.evidence or (s.value or "")
    days = None
    m = re.search(r"(\d+)?\s*(년|개월|달|주|일)", ev)
    if m:
        n = int(m.group(1) or 1)
        days = n * {"년": 365, "개월": 30, "달": 30, "주": 7, "일": 1}[m.group(2)]
    keys = list(REBALANCE)
    if days is not None:
        keys = sorted(_PERIOD_DAYS, key=lambda k: (abs(_PERIOD_DAYS[k] - days), keys.index(k)))[:2]
    opts = [REBALANCE[k] for k in keys]

    def apply(st: State, ans: str) -> None:
        k = _pick(ans, opts)
        if k is not None:
            st.slots.rebalance.value, st.slots.rebalance.status = keys[k], "filled"
    f.questions.append(Question("rebalance:value", "rebalance",
                                f"{_j(_q(ev), '은', '는')} 고를 수 있는 리밸런싱 주기가 아니에요. 어느 쪽으로 할까요?",
                                opts, ev, apply=apply))


# ---- 조건 (비교 하나)

def _operands(c: Comparison) -> list[tuple[str, Operand]]:
    out: list[tuple[str, Operand]] = [("left", c.left)]
    if c.right.value is None and c.right.indicator is not None:
        out.append(("right", c.right))
    return out


def _clause(left: dict, op: str, right: dict) -> str:
    def name(o: dict) -> str:
        if "value" in o:
            return f"{o['value']:g}"
        who = {"self": "", "KOSPI200": "코스피200 ", "KOSDAQ": "코스닥 ", "VIX": ""}.get(o["of"])
        if who is None:
            t = load_tickers().get(o["of"])
            who = f"{t.name if t else o['of']} "
        lab = INDICATORS[o["indicator"]]["label"].format(w=o.get("window"))
        return who + lab
    return f"{name(left)} {name(right)} {_OP_WORD[op]}"


def _set_cmp(st: State, side: str, i: int, left: dict, op: str, right: dict) -> None:
    c = st.slots.entry.value.items[i] if side == "entry" else st.slots.exit.value.items[i]
    c.left = Operand(**left)
    c.op = op
    c.right = RightSide(**right)
    c.status, c.kind = "filled", None
    st.confirmed.add(f"{side}[{i}]")


def _item(st: State, side: str, i: int) -> Comparison:
    return (st.slots.entry if side == "entry" else st.slots.exit).value.items[i]


def _phrase_patch(pid: str, k: int, c: Comparison) -> Optional[tuple[dict, str, dict]]:
    """표현 표 되묻기 구절의 k번째 선택지 → (left, op, right). 선택지 문구는 data/expressions.json 그대로."""
    ma = lambda w, of="self": {"indicator": "ma", "window": w, "of": of}            # noqa: E731
    rsi = {"indicator": "rsi", "window": 14, "of": "self"}
    close = {"indicator": "close", "window": None, "of": "self"}
    v = lambda x: {"value": x}                                                      # noqa: E731
    table = {
        "golden_cross": [(ma(5), "cross_above", ma(20)), (ma(20), "cross_above", ma(60))],
        "dead_cross": [(ma(5), "cross_below", ma(20)), (ma(20), "cross_below", ma(60))],
        "rsi_low_vague": [(rsi, c.op or "<", v(30)), (rsi, c.op or "<", v(20))],
        "volume_spike": [({"indicator": "volume_ratio", "window": 20, "of": "self"}, c.op or "cross_above", v(2)),
                         ({"indicator": "volume_ratio", "window": 20, "of": "self"}, c.op or "cross_above", v(3))],
        "market_fear": [({"indicator": "vix", "window": None, "of": "VIX"}, ">=", v(20)),
                        ({"indicator": "vix", "window": None, "of": "VIX"}, ">=", v(30))],
        "pullback": [(close, "cross_below", ma(20)), (rsi, "<", v(40))],
        "bottom": [(rsi, "<", v(30)), ({"indicator": "ret", "window": 20, "of": "self"}, "<=", v(-10))],
        "top": [(rsi, ">", v(70)), ({"indicator": "ret", "window": 20, "of": "self"}, ">=", v(10))],
    }
    if pid == "ma_vague":
        w = (5, 20, 60)[k]
        left = c.left.model_dump()
        right = c.right.model_dump()
        for o in (left, right):
            if resolve_indicator(o.get("indicator"))[0] == "ma" and o.get("window") is None:
                o["indicator"], o["window"] = "ma", w
        if right.get("indicator") is None and right.get("value") is None:
            right = {"indicator": "ma", "window": w, "of": left.get("of") or "self"}
            if left.get("indicator") in (None, "ma"):
                left = {"indicator": "close", "window": None, "of": right["of"]}
        return left, c.op or op_by_direction(c.evidence) or "cross_above", right
    opts = table.get(pid)
    return opts[k] if opts and k < len(opts) else None


def _q_phrase(side: str, i: int, c: Comparison, p: dict) -> Question:
    text, opts = split_question(p["question"])

    def apply(st: State, ans: str) -> None:
        cur = _item(st, side, i)
        k = _pick(ans, opts)
        if k is not None:
            patch = _phrase_patch(p["id"], k, cur)
            if patch:
                l, op, r = patch
                _set_cmp(st, side, i, l, op, {**{"indicator": None, "window": None, "of": None, "value": None}, **r})
            return
        nums = _numbers(ans)          # 직접 입력: 숫자만 규칙으로 읽는다
        if p["id"] == "ma_vague" and len(nums) == 1:
            cur.right = RightSide(indicator="ma", window=int(nums[0]), of=cur.right.of or "self") \
                if cur.right.value is None else cur.right
            if resolve_indicator(cur.left.indicator)[0] == "ma" and cur.left.window is None:
                cur.left.window = int(nums[0])
            cur.status = "filled"
            st.confirmed.add(f"{side}[{i}]")
        elif p["id"] in ("golden_cross", "dead_cross") and len(nums) == 2:
            a, b = sorted(int(x) for x in nums)
            op = "cross_above" if p["id"] == "golden_cross" else "cross_below"
            _set_cmp(st, side, i, {"indicator": "ma", "window": a, "of": "self"}, op,
                     {"indicator": "ma", "window": b, "of": "self", "value": None})
        elif p["id"] in ("rsi_low_vague", "volume_spike", "market_fear") and len(nums) == 1:
            patch = _phrase_patch(p["id"], 0, cur)
            l, op, _ = patch
            _set_cmp(st, side, i, l, op, {"indicator": None, "window": None, "of": None, "value": nums[0]})
    return Question(f"{side}[{i}]:{p['id']}", side, text, opts, c.evidence, apply=apply)


def _q_bad_window(side: str, i: int, c: Comparison, ind: str, w: int) -> Question:
    near = nearest_windows(ind, w)
    labels = [_window_label(ind, x) for x in near]
    bad = _window_label(ind, w)
    if len(labels) == 2:
        text = f"{_j(bad, '은', '는')} 없어요. {_j(labels[0], '과', '와')} {labels[1]} 중 어느 쪽인가요?"
    else:
        text = f"{_j(bad, '은', '는')} 없어요. {_j(labels[0], '으로', '로')} 할까요?"

    def apply(st: State, ans: str) -> None:
        k = _pick(ans, labels)
        nums = _numbers(ans)
        new = near[k] if k is not None else (int(nums[0]) if len(nums) == 1 else None)
        if new is None:
            return
        cur = _item(st, side, i)
        for o in (cur.left, cur.right):
            if resolve_indicator(o.indicator)[0] == ind and o.window == w:
                o.window = new
        cur.status, cur.kind = "filled", None
        st.confirmed.add(f"{side}[{i}]")
    return Question(f"{side}[{i}]:window", side, text, labels, c.evidence, apply=apply)


def _q_missing_window(side: str, i: int, c: Comparison, ind: str, where: str) -> Question:
    if ind == "ma":
        return _q_phrase(side, i, c, phrase_by_id("ma_vague"))
    allowed = [x for x in INDICATORS[ind]["windows"] if x is not None]
    labels = [_window_label(ind, x) for x in allowed]

    def apply(st: State, ans: str) -> None:
        k = _pick(ans, labels)
        if k is None:
            return
        cur = _item(st, side, i)
        getattr(cur, where).window = allowed[k]
        st.confirmed.add(f"{side}[{i}]:{where}.window")
    label = INDICATORS[ind]["label"].replace("{w}일 ", "").replace("({w}일)", "")
    return Question(f"{side}[{i}]:{where}.window", side, f"{_j(label, '을', '를')} 몇 일 기준으로 볼까요?", labels,
                    c.evidence, apply=apply)


def _q_threshold(side: str, i: int, c: Comparison, ind: str) -> Question:
    low = (c.op or op_by_direction(c.evidence) or "<") in _LOW
    if ind == "rsi":
        if low:
            return _q_phrase(side, i, c, phrase_by_id("rsi_low_vague"))
        text, opts = "RSI가 얼마 위일 때를 높다고 볼까요?", ["70", DIRECT]    # 70 = v0.2 과매수 기본값
    elif ind == "volume_ratio":
        return _q_phrase(side, i, c, phrase_by_id("volume_spike"))
    elif ind == "vix":
        return _q_phrase(side, i, c, phrase_by_id("market_fear"))
    elif ind == "entry_return":
        text = "산 가격보다 몇 % 떨어지면 팔까요?" if low else "산 가격보다 몇 % 오르면 팔까요?"
        opts = [DIRECT]
    elif ind == "holding_days":
        text, opts = "산 뒤 몇 거래일이 지나면 팔까요?", [DIRECT]
    else:
        label = INDICATORS[ind]["label"].format(w=c.left.window)
        text, opts = f"{label} 기준 숫자를 얼마로 할까요?", [DIRECT]

    def apply(st: State, ans: str) -> None:
        nums = _numbers(ans)
        if len(nums) != 1:
            return
        x = nums[0]
        if ind == "entry_return" and low and x > 0:
            x = -x
        cur = _item(st, side, i)
        cur.right = RightSide(value=x)
        if cur.op is None:
            cur.op = op_by_direction(cur.evidence) or ("<=" if low else ">=")
        cur.status = "filled"
        st.confirmed.add(f"{side}[{i}]")
    return Question(f"{side}[{i}]:value", side, text, opts, c.evidence, apply=apply)


def _q_confirm_cmp(side: str, i: int, c: Comparison, clause: str) -> Question:
    def apply(st: State, ans: str) -> None:
        if ans.strip() == "네":
            st.confirmed.add(f"{side}[{i}]")
    hint = c.reason or None
    return Question(f"{side}[{i}]:confirm", side, f"{_j(_q(c.evidence), '을', '를')} '{clause}'{'으' if _batchim(clause) else ''}로 볼까요?",
                    ["네", RESTATE], c.evidence, hint=hint, apply=apply)


def _q_generic(qid: str, slot: str, evidence: str, reason: str = "") -> Question:
    def apply(st: State, ans: str) -> None:
        return None          # 규칙으로 읽을 수 없는 답 → 빈칸이 남는다
    return Question(qid, slot, f"{_j(_q(evidence), '을', '를')} 전략서에 어떻게 적을지 정할 수 없어요. 문장을 고쳐 주시겠어요?",
                    [RESTATE], evidence, hint=reason or None, apply=apply)


def _q_of(side: str, i: int, c: Comparison, where: str, of: str) -> Question:
    def apply(st: State, ans: str) -> None:
        names = _parse_names(ans)
        if len(names) == 1:
            getattr(_item(st, side, i), where).of = names[0].name
    return Question(f"{side}[{i}]:{where}.of", side,
                    f"{_j(_q(of), '은', '는')} 데모 종목 목록에 없어요. 어떤 종목의 값으로 볼까요?", [DIRECT],
                    c.evidence, hint=f"데모 종목: {ticker_names()}", apply=apply)


def _compare(st: State, side: str, i: int, c: Comparison, f: _Found) -> Optional[ResolvedCompare]:
    """비교 하나를 사전과 대조. 거절·질문이면 f 에 넣고 None."""
    cid = f"{side}[{i}]"
    ev = c.evidence or ""
    confirmed = cid in st.confirmed
    has_digit = bool(re.search(r"\d", ev))

    # 1) 사전으로 확인할 수 없는 못 다룸 → 거절
    if not confirmed:
        code = _reject_for(c.status, c.kind, ev) if c.status == "unsupported" or c.kind else None
        if code and code != "UNKNOWN_INDICATOR":
            f.rejections.append(Rejection(code, f"{_q(ev)}: {REJECT_CODES[code]}", ev, cid))
            return None
    # 2) 사전에 없는 지표 → 거절 (LLM 이 사전 id 를 적었어도 '없는 지표'라고 분류했으면 거절)
    for where, o in _operands(c):
        if o.indicator is not None and resolve_indicator(o.indicator)[0] is None:
            f.rejections.append(Rejection("UNKNOWN_INDICATOR",
                                          f"{_j(_q(o.indicator), '은', '는')} 전략 요소 사전에 없는 지표입니다.", ev, cid))
            return None
    if not confirmed and (c.kind == "unknown_indicator" or reject_code_by_phrase(ev) == "UNKNOWN_INDICATOR"):
        f.rejections.append(Rejection("UNKNOWN_INDICATOR", f"{_q(ev)}: 전략 요소 사전에 없는 지표입니다.", ev, cid))
        return None

    # 3) 되묻기 구절 (골든크로스·이평선·RSI 낮을 때·눌림목 …). 숫자를 말했으면 구절이 아니라 값으로 본다.
    if not confirmed and not has_digit and (p := clarify_phrase(ev)):
        f.questions.append(_q_phrase(side, i, c, p))
        return None
    # 기본값 구절 (과매도 = RSI 30, 과매수 = RSI 70)
    if not confirmed and not has_digit:
        for pid in ("oversold", "overbought"):
            p = phrase_by_id(pid)
            if any(pat in ev for pat in p["patterns"]):
                cond = p["condition"]
                return ResolvedCompare(dict(cond["left"]), cond["op"], dict(cond["right"]), ev, defaults=[""])

    # 4) 지표·기간·누구의 값
    defaults: list[str] = []
    ops: dict[str, dict] = {}
    if c.left.indicator is None:
        f.questions.append(_q_generic(f"{cid}:left", side, ev, c.reason))
        return None
    for where, o in _operands(c):
        ind, alias_w = resolve_indicator(o.indicator)
        w = o.window if o.window is not None else alias_w
        allowed = INDICATORS[ind]["windows"]
        if w is None and allowed != [None]:
            if ind == "rsi" or len(allowed) == 1:
                w = DEFAULTS["rsi_window"] if ind == "rsi" else allowed[0]
                defaults.append(f"{where}.window")
            else:
                f.questions.append(_q_missing_window(side, i, c, ind, where))
                return None
        elif w is not None and allowed == [None]:
            f.questions.append(_q_generic(f"{cid}:{where}.window", side, ev, f"{ind} 에는 기간이 없습니다"))
            return None
        elif w is not None and w not in allowed:
            f.questions.append(_q_bad_window(side, i, c, ind, w))   # v0.2: 없는 기간은 거절하지 않고 되묻는다
            return None
        elif (w is not None and (ind == "rsi" or len(allowed) == 1) and alias_w is None
              and str(w) not in re.findall(r"\d+", ev) and f"{cid}" not in st.confirmed):
            defaults.append(f"{where}.window")     # LLM 이 기본 기간(RSI 14)을 스스로 채움 → 원문에 없으니 기본값 표시
        of, of_default = resolve_of(o.of, ind)
        if of is None:
            f.questions.append(_q_of(side, i, c, where, o.of))
            return None
        ops[where] = {"indicator": ind, "window": w, "of": of}

    # 5) 연산자: '~인 동안 / ~하는 순간' 표 (동사로 정하고 되묻지 않는다)
    op = c.op or op_by_direction(ev)
    if op is None:
        f.questions.append(_q_generic(f"{cid}:op", side, ev, "사고파는 동사를 찾지 못했습니다"))
        return None

    # 6) 오른쪽: 숫자 또는 지표
    if "right" in ops:
        right = ops["right"]
    elif c.right.value is not None:
        right = {"value": c.right.value}
    else:
        f.questions.append(_q_threshold(side, i, c, ops["left"]["indicator"]))
        return None

    # 7) LLM 이 애매함·못 다룸이라고 했는데 규칙으로는 다 맞음 → 그대로 쓰지 않고 확인
    if c.status in ("ambiguous", "unsupported") and not confirmed:
        f.questions.append(_q_confirm_cmp(side, i, c, _clause(ops["left"], op, right)))
        return None
    return ResolvedCompare(ops["left"], op, right, ev, defaults)


def _negate(cond: ResolvedCond) -> Optional[ResolvedCond]:
    """조건이 반대가 되면: 비교마다 연산자를 뒤집고 all ↔ any. 산 뒤에만 있는 값(손절·보유 일수)은 뺀다."""
    items = [ResolvedCompare(c.left, _NEGATE[c.op], c.right, c.evidence) for c in cond.items
             if not INDICATORS[c.left["indicator"]].get("position")]
    if not items:
        return None
    return ResolvedCond("any" if cond.logic == "all" else "all", items, source="negate")


def _conditions(st: State, f: _Found) -> None:
    for side in ("entry", "exit"):
        cs: CondSlot = getattr(st.slots, side)
        other = "exit" if side == "entry" else "entry"
        if _vague(cs, side, st):
            code = reject_code_by_phrase(cs.evidence)
            if code:
                f.rejections.append(Rejection(code, f"{_q(cs.evidence)}: {REJECT_CODES[code]}", cs.evidence, side))
            elif not _items(getattr(st.slots, other), other, st):
                f.questions.append(_q_generic(f"{side}:vague", side, cs.evidence, cs.reason))
            # 반대쪽 조건이 있으면 _rules_questions 가 '언제 팔까요/살까요'로 묻는다
            continue
        items = _items(cs, side, st)
        if not items:
            continue
        out = [r for k, c in items if (r := _compare(st, side, k, c, f)) is not None]
        if len(out) == len(items):
            setattr(f.resolved, side, ResolvedCond(cs.value.logic, out))


def _q_no_exit(st: State) -> Question:
    text, opts = split_question(phrase_by_id("no_exit")["question"])

    def apply(st: State, ans: str) -> None:
        k = _pick(ans, opts)
        if k == 0:
            st.derived["exit"] = "negate"
        elif k == 1:
            st.slots.exit.status = "ambiguous"
            st.slots.exit.value.logic = "any"
            st.slots.exit.value.items = [Comparison(status="filled", evidence="", left=Operand(indicator="entry_return",
                                                                                                of="self"),
                                                    op=">=", right=RightSide())]
        elif k == 2:
            st.hold = True
    return Question("exit:none", "exit", text, opts, apply=apply)


def _q_no_entry(st: State, exit_cond: Optional[ResolvedCond]) -> Question:
    text, opts = split_question(phrase_by_id("no_entry")["question"])
    if exit_cond is not None and _negate(exit_cond) is None:
        opts = opts[:1]            # 손절·보유 일수만 있으면 '반대가 되면 다시 사기'를 만들 수 없다

    def apply(st: State, ans: str) -> None:
        k = _pick(ans, opts)
        if k == 0:
            st.start_holding = True
        elif k == 1:
            st.derived["entry"] = "negate"
    return Question("entry:none", "entry", text, opts, apply=apply)


def _rules_questions(st: State, f: _Found) -> None:
    has_entry = bool(_items(st.slots.entry, "entry", st))
    has_exit = bool(_items(st.slots.exit, "exit", st))
    if st.start_holding:
        f.rejections.append(Rejection("UNSUPPORTED_ACTION",
                                      "'처음부터 갖고 시작'은 v0.2 전략서에 적을 칸이 없어 데모에서 실행할 수 없습니다.",
                                      "", "entry"))
        return
    if has_entry and not has_exit:
        if st.derived.get("exit") == "negate":
            if f.resolved.entry is not None:
                f.resolved.exit = _negate(f.resolved.entry)
        elif st.hold:
            f.resolved.hold = True
        else:
            f.questions.append(_q_no_exit(st))
    elif has_exit and not has_entry:
        if st.derived.get("entry") == "negate":
            if f.resolved.exit is not None:
                f.resolved.entry = _negate(f.resolved.exit)
        else:
            f.questions.append(_q_no_entry(st, f.resolved.exit))


# ---- 기간 · 금액 · 비용 · 배당 · 그 밖

def _backtest(st: State, f: _Found, text: str) -> None:
    s = st.slots
    r = f.resolved
    # 기간
    p = s.period
    if p.status == "unspecified":
        r.defaults.append("backtest.period")
    else:
        ok = True
        for k in ("start", "end"):
            v = getattr(p.value, k)
            if v is None:
                r.defaults.append(f"backtest.{k}")
            elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
                setattr(r, k, v)
            else:
                ok = False
        if not ok or p.status in ("ambiguous", "unsupported") and "period" not in st.confirmed:
            opts = [f"최근 3년 ({DEFAULTS['start']} ~ {DEFAULTS['end']})", DIRECT]

            def apply(st: State, ans: str) -> None:
                per = st.slots.period
                if _pick(ans, opts) == 0:
                    per.value.start, per.value.end = DEFAULTS["start"], DEFAULTS["end"]
                else:
                    dates = re.findall(r"\d{4}-\d{2}-\d{2}", ans)
                    years = re.findall(r"(?<!\d)(\d{4})(?!\d|-)", ans)
                    if len(dates) == 2:
                        per.value.start, per.value.end = dates
                    elif len(years) == 2:
                        per.value.start, per.value.end = f"{years[0]}-01-01", f"{years[1]}-12-31"
                    else:
                        return
                per.status = "filled"
                st.confirmed.add("period")
            f.questions.append(Question("period:value", "period", "백테스트 기간을 어떻게 할까요?", opts, p.evidence,
                                        apply=apply))
    # 시작 금액
    c = s.initial_cash
    if c.status == "unspecified":
        r.defaults.append("backtest.initial_cash")
    elif c.status == "filled" and c.value and c.value > 0:
        r.initial_cash = c.value
    else:
        opts = ["1,000만 원", DIRECT]

        def apply(st: State, ans: str) -> None:
            v = 10_000_000 if _pick(ans, opts) == 0 else _parse_money(ans)
            if v:
                st.slots.initial_cash.value, st.slots.initial_cash.status = v, "filled"
        f.questions.append(Question("initial_cash:value", "initial_cash", "시작 금액을 얼마로 할까요?", opts,
                                    c.evidence, apply=apply))
    # 수수료 · 체결 오차
    k = s.costs
    if k.status == "unspecified" or (k.value.commission_pct is None and k.value.slippage_pct is None):
        r.defaults.append("backtest.costs")
    else:
        if k.value.commission_pct is not None and 0 <= k.value.commission_pct < 10:
            r.commission_rate = k.value.commission_pct / 100
        else:
            r.defaults.append("backtest.commission_rate")
        if k.value.slippage_pct is not None and 0 <= k.value.slippage_pct < 10:
            r.slippage_rate = k.value.slippage_pct / 100
        else:
            r.defaults.append("backtest.slippage_rate")
    # 배당: 문장에 배당 낱말이 있을 때만. "배당은 빼줘"처럼 부정이면 LLM 이 false 로 적는다.
    word = any(w in text for w in DIVIDEND_WORDS) if text else False
    r.dividend_mentioned = (s.dividend.value is True) or (word and s.dividend.value is not False)


def _others(st: State, f: _Found) -> None:
    for i, o in enumerate(st.slots.others):
        oid = f"others[{i}]"
        if oid in st.dropped or o.status in ("filled", "unspecified"):
            continue
        code = _reject_for(o.status, o.kind, o.evidence)
        if code:
            f.rejections.append(Rejection(code, f"{_q(o.evidence)}: {REJECT_CODES[code]}", o.evidence, oid))
            continue
        opts = ["빼고 진행", RESTATE]

        def apply(st: State, ans: str, oid=oid) -> None:
            if _pick(ans, opts) == 0:
                st.dropped.add(oid)
        f.questions.append(Question(f"{oid}:drop", "others",
                                    f"{_j(_q(o.evidence), '은', '는')} 전략서의 어느 칸에도 넣을 수 없어요. 빼고 진행할까요?",
                                    opts, o.evidence, hint=o.reason or None, apply=apply))


def _is_empty(st: State) -> bool:
    s = st.slots
    return (not s.assets.value and s.assets.status == "unspecified"
            and not _has_cond(s.entry, "entry", st) and not _has_cond(s.exit, "exit", st)
            and s.rebalance.status == "unspecified" and s.max_weight.status == "unspecified"
            and not any(o.status in ("ambiguous", "unsupported") for o in s.others))


def analyze(st: State, text: str = "") -> _Found:
    f = _Found()
    _others(st, f)
    if _is_empty(st):
        f.rejections.append(Rejection("OUT_OF_DOMAIN", "전략으로 읽을 수 있는 내용(종목·사고파는 조건·비중)이 없습니다.",
                                      text, ""))
        return f
    has_rules = _has_cond(st.slots.entry, "entry", st) or _has_cond(st.slots.exit, "exit", st)
    _asset_questions(st, f, has_rules)
    _max_weight(st, f)
    _conditions(st, f)
    _rules_questions(st, f)
    _rebalance(st, f, has_rules)
    _backtest(st, f, text)
    return f


# ---------------------------------------------------------------- 진입점

def _normalize(st: State) -> None:
    """조건 칸이 애매함인데 비교를 비워 둔 경우("이평선 돌파하면" → items []), 칸 근거에 표현 표 구절
    (되묻기·기본값)이 있으면 빈 비교 하나를 만들어 둔다. 그래야 v0.2 질문(5/20/60일 등)으로 묻는다."""
    for side in ("entry", "exit"):
        cs: CondSlot = getattr(st.slots, side)
        if cs.status == "unspecified" or cs.value.items or not cs.evidence:
            continue
        if reject_code_by_phrase(cs.evidence):
            continue
        defaults = any(any(p in cs.evidence for p in phrase_by_id(x)["patterns"]) for x in ("oversold", "overbought"))
        if clarify_phrase(cs.evidence) or defaults:
            cs.value.items = [Comparison(status="ambiguous", evidence=cs.evidence, reason=cs.reason, left=Operand(),
                                         op=op_by_direction(cs.evidence), right=RightSide())]


def decide(slots: SlotFill, answers: Optional[list[dict[str, str]]] = None, text: str = "") -> Decision:
    """빈칸 상태(+ 되묻기 답) → 진행 / 되묻기 / 거절. text 는 원문 (배당 낱말·근거 표시용)."""
    st = State(slots.model_copy(deep=True))
    _normalize(st)
    answers = list(answers or [])
    for k, round_answers in enumerate(answers):
        f = analyze(st, text)
        if f.rejections:
            return Decision("reject", st, rejection=f.rejections[0], rejections=f.rejections, round=k)
        for q in f.questions[:MAX_QUESTIONS]:
            if q.id in round_answers and q.apply is not None:
                _record(st, q.id, round_answers[q.id])
                q.apply(st, round_answers[q.id])
    f = analyze(st, text)
    if f.rejections:
        return Decision("reject", st, rejection=f.rejections[0], rejections=f.rejections, round=len(answers))
    if f.questions:
        if len(answers) >= MAX_ROUNDS:
            left = " / ".join(q.text for q in f.questions)
            rej = Rejection("UNRESOLVED", f"{MAX_ROUNDS}번 물었는데도 정하지 못한 빈칸이 남았습니다: {left}",
                            "; ".join(q.evidence for q in f.questions if q.evidence),
                            ", ".join(dict.fromkeys(q.slot for q in f.questions)))
            return Decision("reject", st, pending=f.questions, rejection=rej, rejections=[rej], round=len(answers))
        return Decision("clarify", st, questions=f.questions[:MAX_QUESTIONS], pending=f.questions,
                        round=len(answers) + 1)
    return Decision("proceed", st, resolved=f.resolved, round=len(answers))
