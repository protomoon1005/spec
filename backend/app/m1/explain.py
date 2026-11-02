# "AI가 이해한 전략" 설명(설계 문서 "AI가 이해한 전략 설명", 2026-11-02). AI 를 쓰지 않는다.
#
# 설명을 docstring 이 아니라 주석에 두는 이유는 같은 폴더의 다른 파일과 같다(as_of 가드 사정권).
#
# 이름과 달리 AI 가 쓰지 않고 정해진 틀에 값을 끼운다. 그래야 설명 자체가 틀릴 일이 없다.
# 완성된 문장이 아니라 항목별 줄로 만든다. 한국어 조사는 앞 글자 받침에 따라 바뀌어서
# 문장 틀에 값을 끼우면 어색해지기 쉽다. 줄 형식이면 조사가 거의 필요 없다.
# 사용자가 말하지 않은 칸(meta.defaults_used)에는 "(기본값)" 을 붙인다.
from __future__ import annotations

from collections.abc import Mapping

from app.backtest.rules_engine import FEE_RATE, SLIPPAGE_RATE
from app.contracts.strategy_v0_2 import AllOf, Compare, Indicator, StrategyV0_2

INDICATOR_LABELS = {
    "close": "종가",
    "rsi": "RSI(14일)",
    "vol": "20일 변동성",
    "volume_ratio": "거래량(20일 평균 대비)",
    "vix": "VIX",
    "entry_return": "산 가격 대비 수익률",
    "holding_days": "보유 일수",
}
SUBJECT_LABELS = {"KOSPI200": "코스피200", "KOSDAQ": "코스닥", "VIX": ""}
REBALANCE_LABELS = {
    "RB_MONTHLY_FIRST": "매달 첫 거래일",
    "RB_MONTHLY_MID": "매달 중순",
    "RB_QUARTERLY": "분기마다",
    "RB_WEEKLY": "매주 마지막 거래일",
    "RB_THRESHOLD_5": "비중이 5%p 벗어나면",
}
OP_PHRASES = {
    "cross_below": "{r} 아래로 내려가는 순간",
    "cross_above": "{r} 위로 올라가는 순간",
    "<": "{r}보다 낮은 동안",
    "<=": "{r} 이하인 동안",
    ">": "{r}보다 높은 동안",
    ">=": "{r} 이상인 동안",
}


def explain(spec: StrategyV0_2, names: Mapping[str, str]) -> list[str]:
    defaults = set(spec.meta.defaults_used)
    tickers = spec.universe.held_tickers() or []
    lines = [f"대상 종목: {', '.join(_name(t, names) for t in tickers)}"]

    for rule in spec.rules:
        who = "" if rule.target == "each" else f"{_name(rule.target, names)} "
        lines += _condition_lines(f"{who}살 때", rule.entry, names, "삽니다")
        lines += _condition_lines(f"{who}팔 때", rule.exit, names, "팝니다")
        if rule.exit is None:
            lines.append(f"{who}팔 때: 팔지 않고 계속 갖고 있습니다")

    lines.append(_money_split(spec, defaults, names))
    if not spec.is_rules_strategy:
        mark = " (기본값)" if "rebalance" in defaults else ""
        lines.append(f"리밸런싱: {REBALANCE_LABELS.get(spec.rebalance, '없음')}{mark}")
    if spec.constraint.max_weight_per_asset is not None:
        lines.append(f"종목당 최대 비중: {spec.constraint.max_weight_per_asset * 100:g}%")
    bt = spec.backtest
    mark = " (기본값)" if {"period", "capital"} & defaults else ""
    lines.append(f"기간과 금액: {bt.start.isoformat()} ~ {bt.end.isoformat()}, {_won(bt.capital)}{mark}")
    lines.append(f"비용: 수수료 {FEE_RATE * 100:g}%, 체결 오차 {SLIPPAGE_RATE * 100:g}%, 세금 포함 (기본값)")
    lines.append(f"알아 둘 점: {' · '.join(_notes(spec))}")
    return lines


def _condition_lines(head: str, cond, names, verb: str) -> list[str]:
    if cond is None:
        return []
    if isinstance(cond, Compare):
        return [f"{head}: {_compare(cond, names)}. 다음 날 시가에 {verb}"]
    group = "다음이 모두 맞을 때" if isinstance(cond, AllOf) else "다음 중 하나라도 맞을 때"
    items = cond.all if isinstance(cond, AllOf) else cond.any
    return [f"{head}: {group}. 다음 날 시가에 {verb}", *(f"  - {_compare(c, names)}" for c in items)]


def _compare(c: Compare, names) -> str:
    left = _operand(c.left, names)
    right = _operand(c.right, names) if isinstance(c.right, Indicator) else _value(c.left, c.right.value)
    return f"{left} — {OP_PHRASES[c.op].format(r=right)}"


def _operand(op: Indicator, names) -> str:
    if op.indicator == "ma":
        label = f"{op.window}일 이동평균"
    elif op.indicator == "ret":
        label = f"{op.window}일 수익률"
    else:
        label = INDICATOR_LABELS.get(op.indicator, op.indicator)
    if op.of in ("self", "VIX"):
        return label
    subject = SUBJECT_LABELS.get(op.of) or _name(op.of, names)
    return f"{subject} {label}"


def _value(left: Indicator, value: float) -> str:
    if left.indicator in ("ret", "entry_return", "vol"):
        return f"{value * 100:+g}%" if left.indicator != "vol" else f"{value * 100:g}%"
    if left.indicator == "volume_ratio":
        return f"{value:g}배"
    if left.indicator == "holding_days":
        return f"{value:g}거래일"
    return f"{value:g}"


def _money_split(spec: StrategyV0_2, defaults: set[str], names) -> str:
    if isinstance(spec.weights, dict):
        parts = ", ".join(f"{_name(t, names)} {w * 100:g}%" for t, w in spec.weights.items())
        rest = 1 - sum(spec.weights.values())
        tail = f", 나머지 {rest * 100:g}%는 현금" if rest > 1e-9 else ""
        return f"돈 나누기: {parts}{tail}"
    mark = " (기본값)" if "weights" in defaults else ""
    if spec.is_rules_strategy:
        return f"돈 나누기: 종목마다 1/N 자리, 1주 단위{mark}"
    return f"돈 나누기: 똑같이 나눔, 1주 단위{mark}"


def _notes(spec: StrategyV0_2) -> list[str]:
    notes = []
    if spec.backtest.include_dividends:
        notes.append("배당을 반영해 달라고 하셨지만 데모에는 배당 데이터가 없어 반영하지 않습니다")
    else:
        notes.append("배당은 반영하지 않습니다")
    if spec.is_rules_strategy:
        notes.append("끝까지 들고 있는 종목은 팔지 않고 평가만 합니다")
    notes.append("지금 상장된 종목만 있습니다")
    return notes


def _name(ticker: str, names: Mapping[str, str]) -> str:
    return f"{names[ticker]}({ticker})" if ticker in names else ticker


def _won(amount: float) -> str:
    if amount % 100_000_000 == 0:
        return f"{amount / 100_000_000:g}억 원"
    if amount % 10_000 == 0:
        return f"{amount / 10_000:,.0f}만 원"
    return f"{amount:,.0f}원"
