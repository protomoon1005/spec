"""빈칸 값 + 기본값 → 전략서 v0.2 (docs/strategy-spec-v0.2.md "전략서는 어떤 빈칸으로 이루어지나"). 규칙 코드, LLM 미사용.

core.decide 가 '진행'으로 판정한 Resolved 만 받는다. 값을 새로 짐작하지 않고 모양만 바꾼다.
기본값으로 채운 칸은 defaults_applied 에 경로로 남긴다 (화면의 "기본값 사용" 표시).
  빈칸 단위: assets.weight, rebalance, backtest.period, backtest.start, backtest.end, backtest.initial_cash,
            backtest.costs, backtest.commission_rate, backtest.slippage_rate
  비교 단위: rules.entry(.all[i]).left.window (RSI 14일), rules.entry(.all[i]) (과매도 = RSI 30 처럼 비교 전체)
결과는 dict 다. 모양 검사는 core.validate 1단계가 한다 (여기서 Strategy 로 만들다 실패하면 그것도 검사 결과로 보이게).
"""
from __future__ import annotations

from typing import Optional

from core.decide import Resolved, ResolvedCond
from core.grounding import GroundItem, Grounding
from core.schema import SCHEMA_VERSION


def _operand(o: dict) -> dict:
    if "value" in o:
        return {"value": o["value"]}
    d = {"indicator": o["indicator"], "of": o["of"]}
    if o.get("window") is not None:
        d["window"] = o["window"]
    return d


def _cond(rc: Optional[ResolvedCond], base: str, defaults: list[str]) -> Optional[dict]:
    if rc is None or not rc.items:
        return None
    single = len(rc.items) == 1
    out = []
    for i, c in enumerate(rc.items):
        path = base if single else f"{base}.{rc.logic}[{i}]"
        for d in c.defaults:
            defaults.append(f"{path}.{d}" if d else path)
        out.append({"left": _operand(c.left), "op": c.op, "right": _operand(c.right)})
    return out[0] if single else {rc.logic: out}


def grounding(r: Resolved, text: str, answers: Optional[list[dict]] = None) -> Grounding:
    """4단계 근거 검사 입력: 전략서 값마다 LLM 이 적은 근거와 출처 (경로는 assemble 결과와 같다)."""
    items: list[GroundItem] = []
    for i, a in enumerate(r.assets):
        src = a.get("source", "text")
        items.append(GroundItem(f"assets[{i}]", a.get("evidence", ""), src,
                                answer=a.get("evidence", "") if src == "answer" else "",
                                extra=a.get("weight_evidence", "")))
    for base, rc in (("rules.entry", r.entry), ("rules.exit", r.exit)):
        if rc is None or not rc.items:
            continue
        single = len(rc.items) == 1
        for i, c in enumerate(rc.items):
            items.append(GroundItem(base if single else f"{base}.{rc.logic}[{i}]", c.evidence, c.source,
                                    answer=c.answer, op_source=c.op_source, defaults=list(c.defaults)))
    for path, (ev, src) in r.evidence.items():
        items.append(GroundItem(path, ev if src == "text" else "", src, answer=ev if src == "answer" else ""))
    flat = [v for d in (answers or []) for v in d.values()]
    return Grounding(text=text, answers=flat, items=items, handled=list(r.handled), hold=r.hold)


def assemble(r: Resolved) -> dict:
    defaults = list(r.defaults)
    entry = _cond(r.entry, "rules.entry", defaults)
    exit_ = _cond(r.exit, "rules.exit", defaults)
    rules = None
    if entry is not None or exit_ is not None or r.hold:
        rules = {"target": "each", "entry": entry, "exit": exit_}
    return {
        "schema_version": SCHEMA_VERSION,
        "assets": [{"ticker": a["ticker"], "name": a["name"], "weight": a["weight"]} for a in r.assets],
        "max_weight": r.max_weight,
        "rebalance": r.rebalance,
        "rules": rules,
        "backtest": {"start": r.start, "end": r.end, "initial_cash": r.initial_cash,
                     "commission_rate": r.commission_rate, "slippage_rate": r.slippage_rate},
        "defaults_applied": list(dict.fromkeys(defaults)),
    }
