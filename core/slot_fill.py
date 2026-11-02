"""빈칸 채우기 (docs/strategy-spec-v0.2.md "AI가 빈칸마다 적는 네 가지 상태"). 이 저장소에서 LLM 을 쓰는 유일한 단계.

LLM 은 문장을 빈칸에 나눠 담기만 한다. 빈칸마다 상태와 원문 근거를 적는다.
  filled      채워짐 — 문장에 있고 사전 값으로 바꿀 수 있음
  unspecified 말 안 함 — 문장에 없음
  ambiguous   애매함 — 문장에 있지만 뜻이 하나로 정해지지 않음 ("RSI 낮을 때")
  unsupported 못 다룸 — 문장에 있지만 사전에 정확히 맞는 값이 없음 (MACD, 7일선, 목록에 없는 종목)

"바로 진행 / 되묻기 / 거절" 판정은 하지 않는다. 출력 스키마에 판정 칸이 없다 (core.decide 가 한다).
못 다룸(unsupported)일 때 kind 는 '무엇이 사전에 없는지'의 분류일 뿐이고, 거절할지 되물을지는 규칙이 정한다
(예: 사전에 있는 지표인데 기간만 없으면 되묻기).

지표·종목은 자유 문자열로 받는다. enum 으로 막으면 사전에 없는 값을 적을 자리가 없어서 비슷한 값으로
바꿔치기가 일어난다(legacy: LG에너지솔루션 → 카카오, 9일 RSI → RSI 14). 사전에 있는지는 규칙이 본다.

Ollama 설정은 legacy/specify.py 와 같다 (think false, temperature 0, seed 42, num_ctx 16384, 캐시, 응답 시간 측정).
캐시: (모델·옵션·시스템 프롬프트·스키마·입력 문장) 해시 → data/cache/slot_fill/{hash}.json
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.dictionary import INDICATORS, OPERATORS, REBALANCE, load_expressions, load_tickers

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "cache" / "slot_fill"

OLLAMA_URL = "http://localhost:11434"
MODEL = "qwen3:8b"
OPTIONS = {"temperature": 0, "seed": 42, "num_ctx": 16384, "num_predict": 2048}   # num_predict: 출력 폭주 방지 (2026-11-02, 끝나지 않는 생성 관찰)
THINK = False
KEEP_ALIVE = "30m"
TIMEOUT_S = 600

ChatFn = Callable[[list[dict], dict], dict]   # (messages, schema) → Ollama /api/chat 응답 JSON

Status = Literal["filled", "unspecified", "ambiguous", "unsupported"]
STATUSES = ("filled", "unspecified", "ambiguous", "unsupported")

# 못 다룸(unsupported)의 분류. '무엇인지'만 적는다. 규칙(core.decide)이 거절 사유 코드나 되묻기로 바꾼다.
UNSUPPORTED_KINDS: dict[str, str] = {
    "not_in_list": "한국 상장 주식·ETF 이지만 종목 목록에 없음",
    "category": "종목을 이름이 아니라 종류로 말함 (반도체주, 코스피 상위 10종목, 채권 ETF)",
    "foreign_asset": "해외 주식 (애플, 테슬라)",
    "other_product": "주식·ETF 가 아닌 상품 (ETN, 개별 채권, 선물, 옵션, 비트코인)",
    "unknown_indicator": "지표 사전에 없는 지표 (MACD, 볼린저밴드, PER, 뉴스, 수급)",
    "bad_window": "사전에 있는 지표인데 그 기간이 없음 (7일선, 9일 RSI)",
    "future_info": "판단하는 날에는 알 수 없는 정보 (내일 오를 것 같으면, 실적 발표 전에)",
    "trade_method": "지원하지 않는 매매 방식 (공매도, 신용·빚내서, 물타기, 나눠 사기)",
    "intraday": "하루 안에서 시간을 보는 매매 (분봉, 장 시작 10분 뒤, 단타)",
    "not_investment": "투자와 무관한 이야기",
    "other": "그 밖에 사전으로 표현할 수 없는 것",
}
UnsupportedKind = Literal[tuple(UNSUPPORTED_KINDS)]   # type: ignore[valid-type]
OpName = Literal[tuple(OPERATORS)]                     # type: ignore[valid-type]


# 자유 문장 칸 길이 제한. 구조화 출력 문법에 들어가 생성 단계에서 끊긴다.
# (2026-11-02 관찰: temperature 0 에서 reason 칸이 같은 문장을 반복해 출력 상한까지 가는 일이 30문장 중 4건)
REASON_MAX = 80
EVIDENCE_MAX = 120


class OllamaUnavailable(RuntimeError):
    pass


# ---------------------------------------------------------------- 출력 형식 (LLM 구조화 출력 스키마)

class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Operand(_M):
    indicator: Optional[str] = Field(None, description="지표 id (close, ma, rsi ...). 사전에 없으면 원문 이름 그대로")
    window: Optional[int] = Field(None, description="기간(일). 문장에 없으면 null")
    of: Optional[str] = Field(None, description="누구의 값: self | KOSPI200 | KOSDAQ | VIX | 다른 종목 이름")


class RightSide(Operand):
    value: Optional[float] = Field(None, description="숫자와 비교하면 그 숫자. 지표와 비교하면 null")


class Comparison(_M):
    status: Status
    evidence: str = Field(max_length=EVIDENCE_MAX, description="원문 구절 그대로")
    reason: str = Field("", max_length=REASON_MAX)
    kind: Optional[UnsupportedKind] = None
    left: Operand
    op: Optional[OpName] = None
    right: RightSide


class CondValue(_M):
    logic: Literal["all", "any"] = "all"
    items: list[Comparison] = Field(default_factory=list)


class CondSlot(_M):
    status: Status
    value: CondValue = Field(default_factory=CondValue)
    evidence: str = Field("", max_length=EVIDENCE_MAX)
    reason: str = Field("", max_length=REASON_MAX)


class AssetItem(_M):
    status: Status
    said: str = Field(description="원문에 적힌 종목 표기 그대로")
    evidence: str = Field(max_length=EVIDENCE_MAX)
    reason: str = Field("", max_length=REASON_MAX)
    kind: Optional[UnsupportedKind] = None
    weight_pct: Optional[float] = Field(None, description="이 종목 비중(%). 말 안 했으면 null")


class AssetsSlot(_M):
    status: Status
    value: list[AssetItem] = Field(default_factory=list)
    evidence: str = Field("", max_length=EVIDENCE_MAX)
    reason: str = Field("", max_length=REASON_MAX)


class TextSlot(_M):
    status: Status
    value: Optional[str] = None
    evidence: str = Field("", max_length=EVIDENCE_MAX)
    reason: str = Field("", max_length=REASON_MAX)


class NumberSlot(_M):
    status: Status
    value: Optional[float] = None
    evidence: str = Field("", max_length=EVIDENCE_MAX)
    reason: str = Field("", max_length=REASON_MAX)


class Period(_M):
    start: Optional[str] = Field(None, description="YYYY-MM-DD")
    end: Optional[str] = Field(None, description="YYYY-MM-DD")


class PeriodSlot(_M):
    status: Status
    value: Period = Field(default_factory=Period)
    evidence: str = Field("", max_length=EVIDENCE_MAX)
    reason: str = Field("", max_length=REASON_MAX)


class Costs(_M):
    commission_pct: Optional[float] = None
    slippage_pct: Optional[float] = None


class CostsSlot(_M):
    status: Status
    value: Costs = Field(default_factory=Costs)
    evidence: str = Field("", max_length=EVIDENCE_MAX)
    reason: str = Field("", max_length=REASON_MAX)


class BoolSlot(_M):
    status: Status
    value: Optional[bool] = None
    evidence: str = Field("", max_length=EVIDENCE_MAX)
    reason: str = Field("", max_length=REASON_MAX)


class Other(_M):
    """어느 빈칸에도 들어가지 않는 내용 (미래 참조, 공매도, 장중 매매, 투자와 무관 등)."""
    status: Status
    evidence: str = Field(max_length=EVIDENCE_MAX)
    reason: str = Field("", max_length=REASON_MAX)
    kind: Optional[UnsupportedKind] = None


class SlotFill(_M):
    assets: AssetsSlot
    entry: CondSlot
    exit: CondSlot
    rebalance: TextSlot
    max_weight: NumberSlot
    period: PeriodSlot
    initial_cash: NumberSlot
    costs: CostsSlot
    dividend: BoolSlot
    others: list[Other] = Field(default_factory=list)


def _require_all(node):
    """모든 칸을 필수로. 기본값이 있는 칸도 LLM 이 생략하지 못하게 한다
    (2026-11-02 관찰: entry 를 status filled 로 적고 value 를 통째로 빠뜨려 조건이 사라짐)."""
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            node["required"] = list(node["properties"])
        for v in node.values():
            _require_all(v)
    elif isinstance(node, list):
        for v in node:
            _require_all(v)
    return node


def build_json_schema() -> dict:
    return _require_all(SlotFill.model_json_schema())


# ---------------------------------------------------------------- 시스템 프롬프트

def _j(x) -> str:
    return json.dumps(x, ensure_ascii=False)


def _u(status="unspecified", **kw) -> dict:
    return {"status": status, "evidence": "", "reason": "", **kw}


def _empty(**over) -> dict:
    d = {
        "assets": _u(value=[]), "entry": _u(value={"logic": "all", "items": []}),
        "exit": _u(value={"logic": "all", "items": []}), "rebalance": _u(value=None),
        "max_weight": _u(value=None), "period": _u(value={"start": None, "end": None}),
        "initial_cash": _u(value=None), "costs": _u(value={"commission_pct": None, "slippage_pct": None}),
        "dividend": _u(value=None), "others": [],
    }
    d.update(over)
    return d


def _op(indicator=None, window=None, of=None) -> dict:
    return {"indicator": indicator, "window": window, "of": of}


def _rt(indicator=None, window=None, of=None, value=None) -> dict:
    return {"indicator": indicator, "window": window, "of": of, "value": value}


def _cmp(evidence, left, op, right, status="filled", reason="", kind=None) -> dict:
    return {"status": status, "evidence": evidence, "reason": reason, "kind": kind,
            "left": left, "op": op, "right": right}


def _cond(evidence, *items, logic="all") -> dict:
    return {"status": "filled", "value": {"logic": logic, "items": list(items)}, "evidence": evidence, "reason": ""}


def _asset(said, evidence=None, status="filled", weight=None, kind=None, reason="") -> dict:
    return {"status": status, "said": said, "evidence": evidence or said, "reason": reason, "kind": kind,
            "weight_pct": weight}


# 예시 (eval/dev_sentences.jsonl 문장과 겹치지 않게)
EXAMPLES: list[tuple[str, dict]] = [
    ("POSCO홀딩스 10일선 뚫으면 사고 10일선 무너지면 팔아, 손절은 7%. 2022년부터 2024년까지 5천만 원으로",
     _empty(
         assets={"status": "filled", "value": [_asset("POSCO홀딩스")], "evidence": "POSCO홀딩스", "reason": ""},
         entry=_cond("10일선 뚫으면",
                     _cmp("10일선 뚫으면", _op("close", None, "self"), "cross_above", _rt("ma", 10, "self"))),
         exit=_cond("10일선 무너지면 팔아, 손절은 7%",
                    _cmp("10일선 무너지면", _op("close", None, "self"), "cross_below", _rt("ma", 10, "self")),
                    _cmp("손절은 7%", _op("entry_return", None, "self"), "<=", _rt(value=-7)), logic="any"),
         period={"status": "filled", "value": {"start": "2022-01-01", "end": "2024-12-31"},
                 "evidence": "2022년부터 2024년까지", "reason": ""},
         initial_cash={"status": "filled", "value": 50000000, "evidence": "5천만 원으로", "reason": ""})),
    # 사전에 없는 기간 → 비슷한 기간(10일선, 20일선)으로 바꾸지 않고 원문 기간 그대로 + unsupported
    ("카카오 15일선 위면 사고 코스피가 60일선 아래면 팔아",
     _empty(
         assets={"status": "filled", "value": [_asset("카카오")], "evidence": "카카오", "reason": ""},
         entry=_cond("15일선 위면",
                     _cmp("15일선 위면", _op("close", None, "self"), ">", _rt("ma", 15, "self"),
                          status="unsupported", kind="bad_window", reason="이동평균 기간에 15일이 없음")),
         exit=_cond("코스피가 60일선 아래면",
                    _cmp("코스피가 60일선 아래면", _op("close", None, "KOSPI200"), "<", _rt("ma", 60, "KOSPI200"))))),
    # 목록에 없는 종목 → 다른 종목으로 바꾸지 않고 원문 이름 그대로 + unsupported
    ("에코프로 KODEX 200 반반, RSI 낮을 때 사고 20거래일 지나면 팔아",
     _empty(
         assets={"status": "unsupported",
                 "value": [_asset("에코프로", status="unsupported", weight=50, kind="not_in_list",
                                  reason="종목 목록에 없음"),
                           _asset("KODEX 200", weight=50)],
                 "evidence": "에코프로 KODEX 200 반반", "reason": ""},
         entry={"status": "ambiguous", "evidence": "RSI 낮을 때", "reason": "",
                "value": {"logic": "all", "items": [
                    _cmp("RSI 낮을 때", _op("rsi", None, "self"), "<", _rt(),
                         status="ambiguous", reason="기준 숫자가 없음")]}},
         exit=_cond("20거래일 지나면 팔아",
                    _cmp("20거래일 지나면 팔아", _op("holding_days", None, "self"), ">=", _rt(value=20))))),
    # 사전에 없는 지표·매매 방식 → 이름 그대로 + unsupported. 매도 조건을 말하지 않음 → unspecified
    ("반도체주 MACD 골든크로스 나오면 신용으로 사",
     _empty(
         assets={"status": "unsupported",
                 "value": [_asset("반도체주", status="unsupported", kind="category", reason="종류로 말함")],
                 "evidence": "반도체주", "reason": ""},
         entry={"status": "unsupported", "evidence": "MACD 골든크로스 나오면", "reason": "",
                "value": {"logic": "all", "items": [
                    _cmp("MACD 골든크로스 나오면", _op("MACD", None, "self"), "cross_above", _rt(),
                         status="unsupported", kind="unknown_indicator", reason="MACD 는 지표 사전에 없음")]}},
         others=[{"status": "unsupported", "evidence": "신용으로 사", "reason": "신용 매수", "kind": "trade_method"}])),
    ("코스닥 레버리지 70% KODEX 단기채권 30% 담고 분기마다 리밸런싱, 배당은 빼줘",
     _empty(
         assets={"status": "filled",
                 "value": [_asset("코스닥 레버리지", "코스닥 레버리지 70%", weight=70),
                           _asset("KODEX 단기채권", "KODEX 단기채권 30%", weight=30)],
                 "evidence": "코스닥 레버리지 70% KODEX 단기채권 30%", "reason": ""},
         rebalance={"status": "filled", "value": "quarterly", "evidence": "분기마다 리밸런싱", "reason": ""},
         dividend={"status": "filled", "value": False, "evidence": "배당은 빼줘", "reason": ""})),
    ("오늘 점심 메뉴 추천해줘",
     _empty(others=[{"status": "unsupported", "evidence": "오늘 점심 메뉴 추천해줘", "reason": "투자 이야기가 아님",
                     "kind": "not_investment"}])),
]


def _status_of_phrase(verdict: str) -> str:
    return {"ok": "filled", "clarify": "ambiguous", "reject": "unsupported"}[verdict]


def build_system_prompt() -> str:
    ex = load_expressions()
    ind = "\n".join(
        f"- {k}: {v['label'].format(w='N')} — 기간 {'없음' if v['windows'] == [None] else '·'.join(map(str, v['windows'])) + '일'}"
        f"{' (팔 때 조건에서만)' if v.get('exit_only') else ''}" for k, v in INDICATORS.items())
    ops = "\n".join(f"- {k}: {v}" for k, v in OPERATORS.items())
    rebal = "\n".join(f"- {k}: {v}" for k, v in REBALANCE.items())
    tickers = "\n".join(f"- {t.name} ({'주식' if t.kind == 'STOCK' else 'ETF'}) 별칭: {', '.join(t.aliases)}"
                        for t in load_tickers().values())
    kinds = "\n".join(f"- {k}: {v}" for k, v in UNSUPPORTED_KINDS.items())
    direction = "\n".join(
        f"- {', '.join(r['patterns'])} → {r['op']} ({'~인 동안' if r['kind'] == 'state' else '~하는 순간'})"
        for r in ex["direction_rules"]["rules"])
    of_alias = "\n".join(f"- {', '.join(a['patterns'])} → of = {a['of']}" for a in ex["of_aliases"])
    ind_alias = "\n".join(
        f"- {', '.join(a['patterns'])} → {a['indicator']}" + (f" (기간 {a['window']})" if a.get("window") else "")
        + (f"  — {a['note']}" if a.get("note") else "") for a in ex["indicator_aliases"])
    phrases = "\n".join(
        f"- {', '.join(p['patterns'])} → 상태 {_status_of_phrase(p['verdict'])}"
        + (f", 조건 {_j(p['condition'])}" if p.get("condition") else "")
        + (f"  — {p['note']}" if p.get("note") else "")
        for p in ex["phrases"] if p["patterns"])
    examples = "\n\n".join(f"입력: {t}\n출력: {_j(o)}" for t, o in EXAMPLES)
    return f"""너는 한국어 매매 전략 문장을 빈칸에 나눠 담는 도구다. 주어진 JSON Schema 에 맞는 JSON 하나만 출력한다.
너는 '바로 진행/되묻기/거절'을 판단하지 않는다. 빈칸마다 상태와 원문 근거만 적는다. 판단은 다른 프로그램이 한다.

## 빈칸 상태 (모든 빈칸·조건·종목마다 하나)
- filled: 문장에 있고, 아래 사전에 정확히 있는 값으로 바꿀 수 있다
- unspecified: 문장에 없다 (evidence 는 "")
- ambiguous: 문장에 있지만 뜻이 하나로 정해지지 않는다 ("RSI 낮을 때", "이평선 깨지면", "골든크로스", "눌림목")
- unsupported: 문장에 있지만 사전에 정확히 맞는 값이 없다. kind 에 무엇이 없는지 적는다

## 사전에 없는 값 — 비슷한 값으로 바꾸지 마라 (가장 중요)
- 사전에 정확히 맞는 값이 없으면 비슷한 값을 고르지 말고 status 를 unsupported 로 적고, 값은 원문 그대로 적는다.
- 종목 목록에 없는 종목은 said 에 원문 이름 그대로 적는다. 목록의 다른 종목으로 바꾸지 않는다.
- 사전에 없는 지표는 indicator 에 원문 이름 그대로 적는다 ("MACD", "볼린저밴드", "PER").
- 사전에 있는 지표인데 기간이 목록에 없으면 window 에 원문 숫자 그대로 적는다 ("7일선" → ma, window 7, unsupported, kind bad_window).
  14 나 5 같은 가까운 값으로 바꾸지 않는다.
- 문장에 없는 숫자·기간·종목을 지어내지 않는다. 문장에 없으면 null.
- evidence 는 원문에서 글자 그대로 복사한다. 바꿔 쓰거나 요약하지 않는다.

## unsupported 의 kind
{kinds}

## 빈칸
- assets: 종목. value 는 종목마다 {{status, said, evidence, reason, kind, weight_pct}}. 비중을 말했으면 weight_pct(%)
  ("반반" → 50, 50 / "7:3" → 70, 30). 종목을 하나도 말하지 않았으면 status unspecified, value [].
- entry: 살 때 조건, exit: 팔 때 조건. value = {{logic: all|any, items: [비교...]}}. 말하지 않았으면 unspecified, items [].
  비교 하나 = {{status, evidence, reason, kind, left, op, right}}. left = {{indicator, window, of}},
  right = {{indicator, window, of, value}} (숫자와 비교하면 value 에 숫자, indicator 는 null).
  여러 조건이 "그리고"면 all, "또는"/따로 나열된 손절·익절이면 any.
  빈칸 전체의 status 는 비교 중 하나라도 unsupported 면 unsupported, 아니고 ambiguous 가 있으면 ambiguous, 아니면 filled.
- rebalance: 리밸런싱 주기. value 는 아래 id 중 하나. 목록에 없는 주기("1년에 한 번")면 unsupported 이고 value 는 원문.
- max_weight: 종목당 최대 비중(%) ("한 종목에 30% 넘게는 싫어" → 30)
- period: 백테스트 기간 {{start, end}} (YYYY-MM-DD). "2023년부터 2024년까지" → 2023-01-01 ~ 2024-12-31
- initial_cash: 시작 금액(원) ("5천만 원" → 50000000)
- costs: 수수료·체결 오차(%)를 말했을 때만
- dividend: 배당 반영. 배당·고배당·분배금·월배당 낱말이 있으면 true, "배당은 빼줘"처럼 부정이면 false
- others: 위 빈칸 어디에도 들어가지 않는 내용 (미래 참조, 공매도·신용, 장중 매매, 투자와 무관한 말). 없으면 [].

## 지표 사전 (indicator id — 기간)
{ind}
누구의 값(of): self(그 종목) | KOSPI200 | KOSDAQ | VIX | 다른 종목이면 그 종목 이름. 말하지 않았으면 self.

## 연산자 (op)
{ops}

## "~인 동안"과 "~하는 순간" (data/expressions.json — 동사로 정한다)
{direction}

## 누구의 값 표현
{of_alias}

## 지표 표현
{ind_alias}

## 자주 쓰는 구절
{phrases}
({{n}}·{{p}} 는 문장 속 숫자 자리. 손절은 "-{{p}}" 처럼 음수)

## 리밸런싱 주기 id
{rebal}

## 종목 목록 (이 목록에 있는 이름·별칭만 filled. 없는 한국 종목은 unsupported + kind not_in_list)
{tickers}

## 예시
{examples}
"""


# ---------------------------------------------------------------- Ollama 호출

def _ollama_chat(messages: list[dict], schema: dict) -> dict:
    body = {"model": MODEL, "messages": messages, "format": schema, "stream": False,
            "think": THINK, "options": OPTIONS, "keep_alive": KEEP_ALIVE}
    req = urllib.request.Request(f"{OLLAMA_URL}/api/chat", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return json.load(r)
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        raise OllamaUnavailable(f"Ollama({OLLAMA_URL}) 호출 실패: {e}") from e


def ollama_status(timeout: float = 2.0) -> tuple[bool, str]:
    """(사용 가능 여부, 안내 메시지). UI 첫 화면 배너용."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=timeout) as r:
            names = [m["name"] for m in json.load(r).get("models", [])]
    except Exception as e:  # noqa: BLE001 — 어떤 실패든 '사용 불가'로 안내
        return False, f"Ollama 에 연결할 수 없습니다 ({OLLAMA_URL}). `ollama serve` 로 켜 주세요. ({e})"
    if MODEL not in names:
        return False, f"Ollama 에 {MODEL} 모델이 없습니다. `ollama pull {MODEL}` 이 필요합니다."
    return True, f"{MODEL} 사용 가능"


# ---------------------------------------------------------------- 결과 · 캐시

@dataclass
class SlotFillResult:
    input_text: str
    raw: Optional[str]                  # LLM 마지막 출력 원문
    slots: Optional[SlotFill]           # 형식이 맞지 않으면 None
    elapsed_s: float                    # LLM 호출 시간 합계 (캐시 적중이면 원래 호출 시간)
    attempts: int
    cached: bool
    cache_key: str
    meta: dict = field(default_factory=dict)
    error: Optional[str] = None


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def cache_key(text: str, system_prompt: str, schema: dict, retry: Optional[tuple[str, str]] = None) -> str:
    d = {"model": MODEL, "options": OPTIONS, "think": THINK, "system": _sha(system_prompt),
         "schema": _sha(_j(schema)), "input": text.strip()}
    if retry is not None:                 # 4단계 근거 검사 뒤 다시 채우기: (이전 출력, 걸린 내용)
        d["retry"] = [_sha(retry[0]), _sha(retry[1])]
    return _sha(json.dumps(d, sort_keys=True))


def retry_message(feedback: str) -> str:
    """4단계 근거 검사에서 걸린 뒤 다시 채우게 하는 메시지.
    2026-11-02 관찰: 걸린 내용 문구("…에 해당하는 말이 없습니다")를 LLM 이 reason 에 옮겨 적고 사전에 있는 지표를
    unknown_indicator 로 바꿔 거절로 끝났다(ok-08). 그래서 이 문구는 검사 프로그램의 메모일 뿐 값·상태가 아니라고 못박는다."""
    rules = [
        "- 메모는 판단 근거가 아니다. 메모 문구를 reason·kind·값에 옮겨 적지 마라.",
        "- 메모 때문에 status 를 unsupported 나 ambiguous 로 바꾸지 마라. 사전에 있는 지표·종목·숫자면 filled 그대로 둔다.",
        "- 값이 원문과 맞으면 값은 그대로 두고, 그 값을 가리키는 원문 구절을 더 넓게 evidence 에 글자 그대로 적어라 "
        "(예: '10% 떨어지면' → '10% 떨어지면 손절').",
        "- 원문에 없는 값을 적었으면 원문대로 고치고, 원문의 낱말·숫자를 빠뜨렸으면 해당 빈칸에 채워라.",
    ]
    return "\n".join(["아래는 검사 프로그램이 남긴 메모다. 위 출력의 값과 원문 근거(evidence)가 서로 맞는지 "
                      "확인하지 못한 곳을 가리킨다.", "[검사 메모 시작]", feedback, "[검사 메모 끝]", "규칙:", *rules,
                      "같은 스키마로 처음부터 다시 출력하라."])


def _cache_read(key: str, cache_dir: Path) -> Optional[dict]:
    p = cache_dir / f"{key}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _cache_write(key: str, cache_dir: Path, entry: dict) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{key}.json").write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")


def parse(raw: Optional[str]) -> tuple[Optional[SlotFill], Optional[str]]:
    """LLM 출력 → (SlotFill, 오류 메시지)."""
    try:
        return SlotFill.model_validate_json(raw or ""), None
    except ValidationError as e:
        return None, "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()[:10])


def slot_fill(text: str, *, use_cache: bool = True, chat_fn: Optional[ChatFn] = None,
              cache_dir: Path | str = CACHE_DIR, retry: Optional[tuple[str, str]] = None) -> SlotFillResult:
    """한국어 전략 문장 → 빈칸별 상태·값·근거. 형식이 맞지 않으면 오류를 알려 주고 1회만 다시 시킨다.
    retry=(이전 출력 원문, 걸린 내용): 4단계 근거 검사에서 걸린 내용을 알려 주고 한 번 다시 채운다
    (온도 0·시드 고정이라 같은 입력이면 늘 같은 결과. 캐시 키에 둘 다 들어간다)."""
    text = text.strip()
    system = build_system_prompt()
    schema = build_json_schema()
    key = cache_key(text, system, schema, retry)
    cache_dir = Path(cache_dir)

    if use_cache and (hit := _cache_read(key, cache_dir)):
        slots, err = parse(hit["raw"])
        return SlotFillResult(text, hit["raw"], slots, hit["elapsed_s"], hit["attempts"], True, key,
                              hit.get("meta", {}), err)

    chat = chat_fn or _ollama_chat
    messages = [{"role": "system", "content": system}, {"role": "user", "content": text}]
    if retry is not None:
        messages += [{"role": "assistant", "content": retry[0]}, {"role": "user", "content": retry_message(retry[1])}]
    elapsed, attempts, raw, slots, err, meta = 0.0, 0, None, None, None, {"calls": []}
    try:
        for attempt in range(2):
            attempts += 1
            t0 = time.perf_counter()
            resp = chat(messages, schema)
            dt = time.perf_counter() - t0
            elapsed += dt
            raw = resp["message"]["content"]
            meta["calls"].append({k: resp.get(k) for k in
                                  ("prompt_eval_count", "eval_count", "total_duration", "load_duration")}
                                 | {"wall_s": round(dt, 3)})
            slots, err = parse(raw)
            if slots is not None:
                break
            messages += [{"role": "assistant", "content": raw},
                         {"role": "user", "content": f"위 출력이 스키마 형식에 맞지 않는다: {err}\n"
                                                     f"같은 문장을 스키마에 맞게 다시 출력하라."}]
    except OllamaUnavailable as e:
        return SlotFillResult(text, raw, None, elapsed, attempts, False, key, meta, error=str(e))

    prompt_tokens = meta["calls"][0].get("prompt_eval_count")
    if prompt_tokens and prompt_tokens >= OPTIONS["num_ctx"]:
        meta["warning"] = f"프롬프트({prompt_tokens} 토큰)가 num_ctx({OPTIONS['num_ctx']})에 닿아 잘렸을 수 있습니다."
    if use_cache and slots is not None:
        _cache_write(key, cache_dir, {"input": text, "retry_feedback": retry[1] if retry else None, "raw": raw, "elapsed_s": elapsed, "attempts": attempts,
                                      "meta": meta, "model": MODEL,
                                      "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    return SlotFillResult(text, raw, slots, elapsed, attempts, False, key, meta, err)
