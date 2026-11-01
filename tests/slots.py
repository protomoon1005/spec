"""빈칸 상태를 손으로 만드는 헬퍼 (LLM 없이 core.decide · core.pipeline 테스트용)."""
from __future__ import annotations

from core.slot_fill import SlotFill, _empty

_RANK = {"filled": 0, "unspecified": 0, "ambiguous": 1, "unsupported": 2}


def op(indicator=None, window=None, of=None) -> dict:
    return {"indicator": indicator, "window": window, "of": of}


def rt(indicator=None, window=None, of=None, value=None) -> dict:
    return {"indicator": indicator, "window": window, "of": of, "value": value}


def val(x) -> dict:
    return rt(value=x)


def cmp(evidence, left, o, right, status="filled", kind=None, reason="") -> dict:
    return {"status": status, "evidence": evidence, "reason": reason, "kind": kind, "left": left, "op": o,
            "right": right}


def cond(*items, logic="all", status=None, evidence="") -> dict:
    if status is None:
        status = max((i["status"] for i in items), key=lambda s: _RANK[s], default="unspecified")
    return {"status": status, "value": {"logic": logic, "items": list(items)},
            "evidence": evidence or " ".join(i["evidence"] for i in items), "reason": ""}


def asset(said, status="filled", weight=None, kind=None, evidence=None) -> dict:
    return {"status": status, "said": said, "evidence": evidence or said, "reason": "", "kind": kind,
            "weight_pct": weight}


def assets(*items, status=None) -> dict:
    if status is None:
        status = max((i["status"] for i in items), key=lambda s: _RANK[s], default="unspecified")
    return {"status": status, "value": list(items), "evidence": " ".join(i["evidence"] for i in items), "reason": ""}


def slot(value, status="filled", evidence="") -> dict:
    return {"status": status, "value": value, "evidence": evidence, "reason": ""}


def other(evidence, kind=None, status="unsupported") -> dict:
    return {"status": status, "evidence": evidence, "reason": "", "kind": kind}


# 자주 쓰는 비교
RSI30_CROSS = cmp("RSI 30 아래로 떨어지면", op("rsi", 14, "self"), "cross_below", val(30))
MA5_BREAK = cmp("5일선 깨지면", op("close", None, "self"), "cross_below", rt("ma", 5, "self"))
SAMSUNG = asset("삼성전자")


def fill(**over) -> SlotFill:
    return SlotFill.model_validate(_empty(**over))


def basic(**over) -> SlotFill:
    """삼성전자 RSI 30 아래로 떨어지면 사고 5일선 깨지면 팔아."""
    d = {"assets": assets(SAMSUNG), "entry": cond(RSI30_CROSS), "exit": cond(MA5_BREAK)}
    d.update(over)
    return fill(**d)
