"""전략서 v0.2 → 한국어 재서술. 문장 틀에 값을 끼운다 (LLM 금지).

재서술은 "전략서가 실제로 무엇을 하는지"를 보여줘야 하므로 엔진(core/backtest.py)의 동작을 그대로 쓴다.
기본값으로 채운 칸(defaults_applied)은 '(기본값 사용)' 표시 (v0.2 "사용자가 말하지 않은 빈칸 채우기").
끝에 한계 문장을 붙인다: 손절·익절은 종가 기준, 배당 미반영, 생존 편향 (v0.2 "종목", demo-scope "배당").
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from core.backtest import slot_weights
from core.dictionary import (INDICATORS, OF_LABELS, REBALANCE, TAX_CATEGORY_LABELS, UNIT_SUFFIX, indicator_label,
                             load_tickers, of_kind, tax_category)
from core.schema import AllOf, AnyOf, Compare, Cond, Operand, Strategy, Value, iter_compares
from core.tax import rate_history

DEFAULT_TAG = "(기본값 사용)"
SURVIVORSHIP_NOTE = ("상장폐지된 종목은 넣지 않아 지금까지 살아남은 종목만으로 백테스트합니다. "
                     "그래서 성과가 실제보다 좋게 나올 수 있습니다(생존 편향).")
DIVIDEND_NOTE_DEMO = "문장에 배당 이야기가 있지만, 데모에서는 배당 미반영입니다(배당 이력 데이터가 없음)."
DIVIDEND_NOTE = "배당 수익은 반영하지 않았습니다."
CLOSE_BASIS_NOTE = ("손절·익절은 그날 종가로 판단합니다. 장중에 그 가격을 지나도 바로 팔지 않고, "
                    "종가가 조건에 맞으면 다음 거래일 시가에 팝니다. 그래서 실제 손실이 말한 %보다 클 수 있습니다.")

# op → 관형형 ("~인" / "~는")
_OP_ADN = {
    "<=": "이하인", "<": "아래인", ">=": "이상인", ">": "위인",
    "cross_below": "아래로 내려가는", "cross_above": "위로 올라가는",
}


@dataclass
class NarrationPart:
    field: str
    text: str
    is_default: bool = False


@dataclass
class Narration:
    parts: list[NarrationPart]
    notes: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(p.text for p in self.parts)


# ---------------------------------------------------------------- 한국어 헬퍼

def _has_batchim(word: str) -> bool:
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
    return " ".join(([f"{eok:,}억"] if eok else []) + ([f"{man:,}만"] if man else [])) + " 원"


# ---------------------------------------------------------------- 조건 문장

def _who(of: str) -> str:
    kind = of_kind(of)
    if kind == "self":
        return ""
    if kind == "index":
        return (OF_LABELS[of] + " ") if OF_LABELS[of] else ""
    info = load_tickers().get(of)
    return f"{info.name if info else of} "


def _operand(op, left: Operand) -> str:
    if isinstance(op, Value):
        unit = UNIT_SUFFIX[INDICATORS[left.indicator]["unit"]]
        if unit == "원" and of_kind(left.of) == "index":
            unit = ""
        return _num(op.value) + unit
    return _who(op.of) + indicator_label(op.indicator, op.window)


def _clause(c: Compare) -> str:
    subject = _operand(c.left, c.left)
    return f"{_josa(subject, '이', '가')} {_operand(c.right, c.left)} {_OP_ADN[c.op]}"


def _cond(cond: Cond, top: bool = True) -> str:
    if isinstance(cond, Compare):
        return _clause(cond)
    items, word = (cond.all, "그리고") if isinstance(cond, AllOf) else (cond.any, "또는")
    body = f" {word} ".join(_cond(c, top=False) for c in items)
    return body if top else f"({body})"


def _day(cond: Cond) -> str:
    if isinstance(cond, Compare):
        return f"**{_cond(cond)} 날**"
    head = "다음이 모두 맞는 날" if isinstance(cond, AllOf) else "다음 중 하나라도 맞는 날"
    return f"**{head}: {_cond(cond)}**"


# ---------------------------------------------------------------- 본문

def _is_default(spec: Strategy, prefix: str) -> bool:
    return any(d == prefix or d.startswith(prefix + ".") or d.startswith(prefix + "[") for d in spec.defaults_applied)


def _tag(spec: Strategy, prefix: str) -> str:
    return DEFAULT_TAG if _is_default(spec, prefix) else ""


def _assets_part(spec: Strategy) -> NarrationPart:
    names = ", ".join(f"**{a.name}**" for a in spec.assets)
    ws = slot_weights(spec)
    tag = _tag(spec, "assets")
    if spec.rules is None:
        alloc = ", ".join(f"**{a.name}** {_pct(w)}" for a, w in zip(spec.assets, ws))
        text = f"{alloc}로 나눠 담습니다{tag}."
    elif all(a.weight is None for a in spec.assets):
        n = len(spec.assets)
        text = f"대상 종목은 {names}입니다{tag}. 종목마다 총자산의 1/{n}씩 자리를 정해 두고, 조건이 맞은 종목만 자기 자리만큼 삽니다."
    else:
        alloc = ", ".join(f"**{a.name}** {_pct(w)}" for a, w in zip(spec.assets, ws))
        text = f"대상 종목과 자리는 {alloc}입니다{tag}. 조건이 맞은 종목만 자기 자리만큼 삽니다."
    if spec.max_weight is not None:
        text += f" 종목당 최대 {_pct(spec.max_weight)}까지만 담습니다."
    rest = 1 - sum(ws)
    if rest > 1e-9 or spec.rules is not None:
        text += " 빈 자리는 현금으로 둡니다(이자 없음)." if spec.rules is not None else f" 나머지 {_pct(rest)}는 현금으로 둡니다."
    return NarrationPart("assets", text, bool(tag))


def _rules_parts(spec: Strategy) -> list[NarrationPart]:
    r = spec.rules
    parts: list[NarrationPart] = []
    if r.entry is not None:
        parts.append(NarrationPart("rules.entry",
                                   f"{_day(r.entry)}{_tag(spec, 'rules.entry')} 다음 거래일 시가에 그 종목을 삽니다 "
                                   f"(1주 단위, 새 종목을 살 때 갖고 있는 종목은 팔지 않습니다)."))
    if r.exit is not None:
        parts.append(NarrationPart("rules.exit",
                                   f"{_day(r.exit)}{_tag(spec, 'rules.exit')} 다음 거래일 시가에 그 종목을 모두 팝니다."))
    else:
        parts.append(NarrationPart("rules.exit", "팔 때 조건이 없어, 산 종목은 기간 끝까지 갖고 있습니다."))
    parts.append(NarrationPart("rules",
                               "조건은 종목마다 따로 봅니다. 같은 날 사는 조건과 파는 조건이 함께 맞으면 팔기를 우선합니다"
                               "(갖고 있으면 팔고, 없으면 사지 않습니다)."))
    if spec.rebalance is not None:
        parts.append(NarrationPart("rebalance",
                                   f"{REBALANCE[spec.rebalance]} 종가를 보고, 갖고 있는 종목을 다음 거래일 시가에 "
                                   f"자기 자리 비중으로 다시 맞춥니다{_tag(spec, 'rebalance')}."))
    return parts


def _allocation_part(spec: Strategy) -> NarrationPart:
    return NarrationPart("rebalance",
                         f"기간 첫 거래일 시가에 위 비중대로 사고, {REBALANCE[spec.rebalance]} 종가를 보고 "
                         f"다음 거래일 시가에 비중을 다시 맞춥니다{_tag(spec, 'rebalance')}.")


def _settle_part() -> NarrationPart:
    return NarrationPart("backtest",
                         "판단은 모두 그날 종가까지의 데이터로 하고, 체결은 다음 거래일 시가에 합니다. "
                         "기간 마지막 날까지 남은 보유분은 팔지 않고 마지막 날 종가로 평가만 합니다(매도 비용·세금 미반영).")


def _backtest_part(spec: Strategy) -> NarrationPart:
    b = spec.backtest
    tag = _tag(spec, "backtest")
    return NarrationPart("backtest",
                         f"기간 {b.start} ~ {b.end}, 시작 금액 {_won(b.initial_cash)}{tag}. "
                         f"비용: 수수료 {_pct(b.commission_rate)}(살 때·팔 때 각각), 체결 오차 {_pct(b.slippage_rate)}.",
                         bool(tag))


def _tax_part(spec: Strategy) -> NarrationPart:
    cats: list[str] = []
    for a in spec.assets:
        c = tax_category(a.ticker)
        if c and c not in cats:
            cats.append(c)
    bits: list[str] = []
    for c in cats:
        label = TAX_CATEGORY_LABELS[c]
        if c.endswith("_STOCK"):
            hist = "·".join(f"{_pct(rate)}({s.year}{'' if e is not None else '~'})" for s, e, rate in rate_history(c))
            bits.append(f"{label}은 팔 때 판 금액의 {hist}")
        elif c == "ETF_DOMESTIC_EQUITY":
            bits.append(f"{label}는 세금 없음")
        else:
            bits.append(f"{label}는 팔아서 남은 차익의 15.4%(손해 본 거래는 없음)")
    body = ", ".join(bits) if bits else "종목 목록에 없는 종목이라 세율을 정할 수 없음"
    return NarrationPart("tax", f"세금은 거래한 날짜의 세율을 씁니다: {body}.")


def _uses_entry_return(spec: Strategy) -> bool:
    if spec.rules is None:
        return False
    return any(isinstance(op, Operand) and op.indicator == "entry_return"
               for _, c in iter_compares(spec.rules.exit, "rules.exit") for op in (c.left, c.right))


def _limit_parts(spec: Strategy, dividend_mentioned: bool) -> list[NarrationPart]:
    parts = []
    if _uses_entry_return(spec):
        parts.append(NarrationPart("limits.close_basis", CLOSE_BASIS_NOTE))
    parts.append(NarrationPart("limits.dividend", DIVIDEND_NOTE_DEMO if dividend_mentioned else DIVIDEND_NOTE))
    parts.append(NarrationPart("limits.survivorship", SURVIVORSHIP_NOTE))
    return parts


def narrate(spec: Strategy, *, dividend_mentioned: bool = False) -> Narration:
    """검사를 통과한 전략서를 한국어로 다시 씁니다. dividend_mentioned: 문장에 배당 낱말이 있었나."""
    parts = [_assets_part(spec)]
    if spec.rules is not None:
        parts += _rules_parts(spec)
    elif spec.rebalance is not None:
        parts.append(_allocation_part(spec))
    parts += [_settle_part(), _backtest_part(spec), _tax_part(spec)]
    limits = _limit_parts(spec, dividend_mentioned)
    return Narration(parts=parts + limits, notes=[p.text for p in limits])
