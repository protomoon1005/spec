"""명세 → 한국어 재서술 (docs/demo-spec.md §B6). 템플릿만 사용, LLM 금지.

재서술은 "명세가 실제로 무엇을 하는지"를 보여줘야 하므로 엔진(core/backtest.py)의 동작을 그대로 쓴다.
- 신호는 T일 종가까지로 판정, T+1 시가 체결
- 손절·익절은 종가 기준 판정 (장중 가격 미사용)
- 기간 종료 시 미청산 포지션은 마지막 날 종가로 평가만 함 (강제 청산·매도 비용 없음)
- 기본값이 적용된 부분은 '(기본값)' 표시 + 마지막 줄 안내
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from legacy.backtest import load_costs
from legacy.dictionary import INDICATORS, load_tickers
from legacy.schema import Condition, DemoSpec, RefMul

DEFAULT_TAG = "(기본값)"

# op → (어간, 연결 'and', 연결 'or', 관형형)
_OP_FORMS = {
    "lt": ("아래", "이고", "이거나", "인"),
    "gt": ("위", "이고", "이거나", "인"),
    "cross_below": ("아래로 내려가", "고", "거나", "는"),
    "cross_above": ("위로 올라가", "고", "거나", "는"),
}

_DEFAULT_LABELS = {"universe": "대상 종목", "backtest": "기간·초기 자금", "costs": "수수료·세율"}


@dataclass
class NarrationPart:
    field: str          # 이 문장이 설명하는 명세 필드 (UI 하이라이트용)
    text: str
    is_default: bool = False


@dataclass
class Narration:
    parts: list[NarrationPart]
    default_note: Optional[str] = None
    notes: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        body = " ".join(p.text for p in self.parts)
        return body + (f"\n\n{self.default_note}" if self.default_note else "")


# ---------------------------------------------------------------- 한국어 헬퍼

def _has_batchim(word: str) -> bool:
    """괄호 설명을 뺀 마지막 글자 기준. 한글이 아니면(RSI 등) 받침 없음으로 본다."""
    w = re.sub(r"\([^)]*\)", "", word).strip()
    ch = w[-1] if w else ""
    if "가" <= ch <= "힣":
        return (ord(ch) - 0xAC00) % 28 != 0
    return False


def _josa(word: str, with_b: str, without_b: str) -> str:
    return word + (with_b if _has_batchim(word) else without_b)


def _num(v: float) -> str:
    return f"{v:,.0f}" if float(v).is_integer() else f"{v:,g}"


def _pct(ratio: float) -> str:
    return f"{round(ratio * 100, 4):g}%"


def _won(amount: float) -> str:
    a = int(round(amount))
    if a != amount or a % 10_000:
        return f"{_num(amount)}원"
    eok, man = divmod(a // 10_000, 10_000)
    parts = ([f"{eok:,}억"] if eok else []) + ([f"{man:,}만"] if man else [])
    return " ".join(parts) + " 원"


# ---------------------------------------------------------------- 조건 문장

def _operand(left: str, x) -> str:
    if isinstance(x, RefMul):
        return f"{INDICATORS[x.ref]['label']}의 {x.mul:g}배"
    if isinstance(x, str):
        return INDICATORS[x]["label"]
    unit = {"price": "원", "volume": "주"}.get(INDICATORS[left]["family"], "")
    return _num(x) + unit


def _clause(c: Condition, form: int) -> str:
    """form: 1=and 연결형, 2=or 연결형, 3=관형형('~인'/'~는')."""
    stem = _OP_FORMS[c.op]
    return f"{_josa(INDICATORS[c.left]['label'], '이', '가')} {_operand(c.left, c.right)} {stem[0]}{stem[form]}"


def _day_phrase(conds: list[Condition], logic: str) -> str:
    """조건 목록 → '…인 날' / '…이고 …는 날'."""
    link = 1 if logic == "all" else 2
    clauses = [_clause(c, link) for c in conds[:-1]] + [_clause(conds[-1], 3)]
    return " ".join(clauses) + " 날"


# ---------------------------------------------------------------- 본문

def _defaults(spec: DemoSpec) -> list[str]:
    return list(spec.provenance.defaults_applied) if spec.provenance else []


def _is_default(defaults: list[str], prefix: str) -> bool:
    return any(d == prefix or d.startswith(prefix + ".") or d.startswith(prefix + "[") for d in defaults)


def _cond_defaults(defaults: list[str], side: str, n: int) -> bool:
    return any(_is_default(defaults, f"{side}.conditions[{i}]") for i in range(n))


def _universe_part(spec: DemoSpec, defaults: list[str]) -> NarrationPart:
    tag = DEFAULT_TAG if _is_default(defaults, "universe") else ""
    u = spec.universe
    if len(u) == 1 and abs(u[0].weight - 1) < 1e-9:
        text = f"**{u[0].name}** 한 종목에 초기 자금 전액을 배정합니다{tag}."
    else:
        alloc = ", ".join(f"**{x.name}**에 {_pct(x.weight)}" for x in u)
        text = f"초기 자금을 {alloc} 배정합니다{tag}."
        rest = 1 - sum(x.weight for x in u)
        if rest > 1e-9:
            text += f" 나머지 {_pct(rest)}는 현금으로 둡니다."
        if len(u) > 1:
            text += " 아래 규칙은 종목마다 따로 적용하며, 종목 사이에 자금을 옮기지 않습니다."
    return NarrationPart("universe", text, bool(tag))


def _entry_part(spec: DemoSpec, defaults: list[str]) -> NarrationPart:
    e = spec.entry
    tag = DEFAULT_TAG if _cond_defaults(defaults, "entry", len(e.conditions)) else ""
    text = (f"**{_day_phrase(e.conditions, e.logic)}**{tag} "
            f"다음 거래일 시가에 배정 자금 전액으로 매수합니다(정수 주 단위, 남는 돈은 현금). "
            f"이미 보유 중이면 추가로 사지 않습니다.")
    return NarrationPart("entry", text, bool(tag))


def _exit_part(spec: DemoSpec, defaults: list[str]) -> NarrationPart:
    x = spec.exit
    items: list[str] = []
    if x.conditions:
        tag = DEFAULT_TAG if _cond_defaults(defaults, "exit", len(x.conditions)) else ""
        items.append(f"**{_day_phrase(x.conditions, x.logic)}**{tag}")
    if x.stop_loss is not None:
        items.append(f"**종가가 매수가보다 {_pct(x.stop_loss)} 이상 낮은 날**(손절)")
    if x.take_profit is not None:
        items.append(f"**종가가 매수가보다 {_pct(x.take_profit)} 이상 높은 날**(익절)")
    if x.max_hold_days is not None:
        items.append(f"**매수 체결일을 1일째로 세어 {x.max_hold_days}거래일째 되는 날**(보유기간)")
    if len(items) == 1:
        text = f"{items[0]} 다음 거래일 시가에 전량 매도합니다."
    else:
        text = f"다음 중 하나라도 해당하는 날, 다음 거래일 시가에 전량 매도합니다: {', '.join(items)}."
    basis = [n for n, v in (("손절", x.stop_loss), ("익절", x.take_profit)) if v is not None]
    if basis:
        text += f" {'·'.join(basis)}은 장중 가격이 아니라 그날 종가로 판단합니다."
    return NarrationPart("exit", text)


def _settle_part() -> NarrationPart:
    return NarrationPart(
        "backtest",
        "매수·매도 신호는 모두 그날 종가까지의 데이터로 판단합니다. "
        "기간 마지막 날까지 팔리지 않은 보유분은 강제로 팔지 않고 마지막 날 종가로 평가만 합니다"
        "(매도 수수료·세금 미반영). 마지막 날 나온 신호는 체결할 다음 날이 없어 실행하지 않습니다.",
    )


def _backtest_part(spec: DemoSpec, defaults: list[str]) -> NarrationPart:
    b = spec.backtest
    tag = DEFAULT_TAG if _is_default(defaults, "backtest") else ""
    return NarrationPart("backtest", f"기간 {b.start} ~ {b.end}, 초기 자금 {_won(b.initial_cash)}{tag}.", bool(tag))


def _costs_part(spec: DemoSpec, defaults: list[str], costs_config: Optional[dict]) -> tuple[NarrationPart, list[str]]:
    cfg = costs_config or load_costs()
    profile = cfg["tax_profiles"][spec.costs.tax_profile]
    default_type = cfg.get("default_asset_type", "stock")
    tickers = load_tickers()
    notes: list[str] = []
    taxes: list[tuple[str, float, str]] = []
    for u in spec.universe:
        info = tickers.get(u.ticker)
        atype = info.asset_type if info else default_type
        if info is None:
            notes.append(f"{u.name}({u.ticker})은 종목 사전에 없어 {default_type} 세율을 적용합니다.")
        taxes.append((u.name, float(profile[atype]), atype))

    tag = DEFAULT_TAG if _is_default(defaults, "costs") else ""

    def tax_str(rate: float, atype: str) -> str:
        return f"{rate * 100:.2f}%" if rate > 0 else ("없음(ETF)" if atype == "etf" else "없음")

    if len(taxes) == 1:
        tax = tax_str(taxes[0][1], taxes[0][2])
    else:
        tax = " / ".join(f"{n} {tax_str(r, a)}" for n, r, a in taxes)
    text = (f"비용{tag}: 수수료 매수·매도 각 {_pct(spec.costs.commission_bps / 10_000)}, "
            f"매도 시 거래세 {tax}. 슬리피지는 반영하지 않습니다.")
    return NarrationPart("costs", text, bool(tag)), notes


def _default_note(defaults: list[str]) -> Optional[str]:
    if not defaults:
        return None
    labels: list[str] = []
    for d in defaults:
        m = re.match(r"(entry|exit)\.conditions\[(\d+)\]", d)
        if m:
            label = f"{'매수' if m.group(1) == 'entry' else '매도'} 조건 {int(m.group(2)) + 1}의 기간·기준값"
        else:
            label = _DEFAULT_LABELS.get(d.split(".")[0].split("[")[0], d)
        if label not in labels:
            labels.append(label)
    joined = ", ".join(labels)
    return f"※ {_josa(joined, '은', '는')} 문장에 언급이 없어 기본값을 적용했습니다."


def narrate(spec: DemoSpec, costs_config: Optional[dict] = None) -> Narration:
    """검증기를 통과한(verdict=ok) 명세를 한국어로 재서술한다."""
    if spec.verdict != "ok" or spec.entry is None or spec.exit is None or not spec.universe:
        raise ValueError("실행 가능한 명세(verdict=ok, universe·entry·exit 존재)만 재서술합니다.")
    d = _defaults(spec)
    costs, notes = _costs_part(spec, d, costs_config)
    parts = [
        _universe_part(spec, d),
        _entry_part(spec, d),
        _exit_part(spec, d),
        _settle_part(),
        _backtest_part(spec, d),
        costs,
    ]
    return Narration(parts=parts, default_note=_default_note(d), notes=notes)
