#!/usr/bin/env python3
"""조각 찾기가 평가 데이터셋 개발용(dev) 문장에서 필요한 재료를 얼마나 잡는지 잰다.

설계 문서 "확정 전에 잴 것" 1번. v0.2 구현 3단계(docs/v0.2-plan.md)의 끝난 기준이다.
최종 평가용(test) 문장은 읽지 않는다(eval/README.md).

재료(atom)는 정답에서 뽑는다.
  ok      정답 전략서의 종목, 지표와 기간, 숫자, 비교 방향, 리밸런싱, 종목별 비중, 배당, 기간 · 시작 금액,
          업종 고르기(업종 이름, 고르는 방식, 개수)
  reject  거절 사전으로 잡는 사유(UNKNOWN_INDICATOR, UNSUPPORTED_ASSET, UNSUPPORTED_ACTION, INTRADAY,
          FUTURE_REF)는 그 사유의 거절 조각. NO_DATA 는 종목 조각. VALIDATION · OUT_OF_DOMAIN 은 재지 않는다
  ask     되물을 거리마다 그 판단에 필요한 조각(예: ma_window 는 기간 없는 이평 조각)
종가(close)는 "5일선 깨지면" 처럼 말하지 않아도 들어가므로 재료에서 뺀다.
말하지 않아 기본값으로 채운 칸(정답의 defaults_used)도 문장에 없으므로 뺀다.

실행 (api 컨테이너, 종목 원장이 든 DB 필요):
    docker compose exec api python /repo/scripts/measure_spans.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))

from app.m1 import spans  # noqa: E402
from app.repositories import etf_master  # noqa: E402

DATASET = REPO / "eval" / "dataset.jsonl"
DICTIONARY_REJECTS = {
    "UNKNOWN_INDICATOR", "UNSUPPORTED_ASSET", "UNSUPPORTED_ACTION", "INTRADAY", "FUTURE_REF",
}
DEFAULT_BACKTEST = {"start": "2023-01-01", "end": "2025-12-31", "capital": 10000000}


def main() -> int:
    rows = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    dev = [r for r in rows if r["split"] == "dev"]
    dictionary = spans.load_dictionary()
    records = [(r.ticker, r.name) for r in etf_master.list_all(include_leveraged=True)]
    index = spans.InstrumentIndex(records, dictionary)
    print(f"사전 {dictionary.version}, 종목 {len(records)}, dev {len(dev)}건\n")

    found_total = need_total = 0
    by_type: dict[str, list[int]] = {}
    for row in dev:
        result = spans.find_spans(row["text"], dictionary, index)
        atoms = _atoms(row)
        missing = [a for a in atoms if not _has(a, result)]
        found = len(atoms) - len(missing)
        found_total += found
        need_total += len(atoms)
        by_type.setdefault(row["type"], [0, 0])
        by_type[row["type"]][0] += found
        by_type[row["type"]][1] += len(atoms)
        mark = "OK  " if not missing else "MISS"
        print(f"{mark} {row['id']} {row['type']:6} {found}/{len(atoms)} | {row['text']}")
        print("       " + " ".join(f"[{s.label()}:{s.kind}]" for s in result.spans))
        for atom in missing:
            print(f"       빠짐: {atom}")
    print()
    for kind, (found, need) in sorted(by_type.items()):
        print(f"{kind:6} {found}/{need} = {found / need:.1%}" if need else f"{kind:6} 잴 재료 없음")
    print(f"전체   {found_total}/{need_total} = {found_total / need_total:.1%}")
    return 0


def _atoms(row: dict) -> list[tuple]:
    atoms: list[tuple] = []
    if row["type"] == "ok":
        spec = row["expected_spec"]
        universe = spec["universe"]
        for ticker in universe.get("tickers", []):
            atoms.append(("instrument", ticker))
        if "selector" in universe:
            sel = universe["selector"]
            atoms.append(("sector", sel["value"]))
            if sel.get("method"):
                atoms.append(("selection", sel["method"]))
            if isinstance(sel.get("count"), int):
                atoms.append(("count", sel["count"]))
        for rule in spec["rules"]:
            for side in ("entry", "exit"):
                cond = rule.get(side)
                if cond is None:
                    continue
                for compare in cond.get("all") or cond.get("any") or [cond]:
                    atoms.extend(_compare_atoms(compare))
        # 말하지 않아 기본값으로 채운 칸(defaults_used)은 문장에 없으므로 재지 않는다
        defaults = set(row.get("defaults_used") or [])
        if spec.get("rebalance") and "rebalance" not in defaults:
            atoms.append(("rebalance", spec["rebalance"]))
        if isinstance(spec["weights"], dict):
            values = sorted(set(spec["weights"].values()))
            if values == [0.5] and len(spec["weights"]) == 2:
                atoms.append(("half", None))
            else:
                atoms.extend(("percent", round(v * 100, 6)) for v in spec["weights"].values())
        backtest = spec["backtest"]
        if backtest["include_dividends"]:
            atoms.append(("dividend", True))
        if backtest["start"] != DEFAULT_BACKTEST["start"] or backtest["end"] != DEFAULT_BACKTEST["end"]:
            atoms.append(("period", backtest["start"][:4]))
        if backtest["capital"] != DEFAULT_BACKTEST["capital"]:
            atoms.append(("capital", backtest["capital"]))
    elif row["type"] == "reject":
        code = row["expected_reject"]["code"]
        if code in DICTIONARY_REJECTS:
            atoms.append(("reject", code))
        elif code == "NO_DATA":
            atoms.append(("instrument_any", None))
    else:
        for ask in row["expected_ask"]:
            atoms.append(("ask", ask["slot"]))
    return atoms


def _compare_atoms(compare: dict) -> list[tuple]:
    out: list[tuple] = []
    left, right, op = compare["left"], compare["right"], compare["op"]
    for operand in (left, right):
        if "value" in operand:
            out.append(("value", operand["value"], left.get("indicator")))
            continue
        name = operand["indicator"]
        if operand.get("of") in ("KOSPI200", "KOSDAQ"):
            out.append(("subject", operand["of"]))
        if name == "close":
            continue
        out.append(("indicator", name, operand.get("window")))
    out.append(("op", op, left.get("indicator")))
    return out


def _has(atom: tuple, result: spans.SpanResult) -> bool:
    kind = atom[0]
    spans_ = result.spans
    if kind == "instrument":
        return any(s.kind == "instrument" and s.meaning.get("ticker") == atom[1] for s in spans_)
    if kind == "instrument_any":
        return any(s.kind == "instrument" for s in spans_)
    if kind == "indicator":
        _, name, window = atom
        for s in spans_:
            if s.kind != "indicator":
                continue
            m = s.meaning
            if name == "entry_return" and s.entry_id == "entry_return":
                return True
            if m.get("indicator") == name and (m.get("window") in (window, None)):
                return True
        # 익절 · 손절만 말해도 산 가격 대비 수익률이다
        return name == "entry_return" and any(s.entry_id in ("take_profit", "stop_loss") for s in spans_)
    if kind == "value":
        _, value, indicator = atom
        for s in spans_:
            m = s.meaning
            if "value" not in m or not isinstance(m["value"], (int, float)):
                continue
            number = m["value"] / 100 if m.get("unit") == "percent" and indicator in ("ret", "entry_return") \
                else m["value"]
            if abs(abs(number) - abs(value)) < 1e-9:
                return True
        return False
    if kind == "op":
        _, op, indicator = atom
        if any(s.kind == "verb" and s.meaning.get("op") == op for s in spans_):
            return True
        if indicator == "entry_return":
            return any(s.meaning.get("op") == op for s in spans_ if s.kind == "action")
        return any(s.kind == "indicator" and s.meaning.get("op") == op for s in spans_)
    if kind == "subject":
        return any(s.kind == "subject" and s.meaning.get("of") == atom[1] for s in spans_)
    if kind == "rebalance":
        for s in spans_:
            r = s.meaning.get("rebalance")
            if r == atom[1] or (atom[1] == "RB_THRESHOLD_5" and r == "drift" and s.meaning.get("value") == 5):
                return True
        return False
    if kind == "percent":
        return any(s.meaning.get("unit") == "percent" and s.meaning.get("value") == atom[1] for s in spans_)
    if kind == "half":
        # "반반" 이나 "50%" 두 번
        return any(s.kind == "weight" and s.meaning.get("weights") == "half" for s in spans_) or sum(
            1 for s in spans_ if s.meaning.get("unit") == "percent" and s.meaning.get("value") == 50
        ) == 2
    if kind == "dividend":
        return any(s.kind == "dividend" for s in spans_)
    if kind == "period":
        return any(s.kind == "period" and str(s.meaning.get("year")) == atom[1] for s in spans_)
    if kind == "capital":
        return any(s.kind == "capital" and s.meaning.get("unit") == "won" for s in spans_)
    if kind == "sector":
        return any(s.kind == "sector" and _same_sector(s.meaning.get("sector"), atom[1]) for s in spans_)
    if kind == "selection":
        return any(s.kind == "selection" and s.meaning.get("method") == atom[1] for s in spans_)
    if kind == "count":
        return any(s.meaning.get("unit") == "count" and s.meaning.get("value") == atom[1] for s in spans_)
    if kind == "reject":
        return any(s.kind == "reject" and s.meaning.get("reject") == atom[1] for s in spans_)
    if kind == "ask":
        return _ask_has(atom[1], result)
    raise ValueError(atom)


def _same_sector(found, expected) -> bool:
    return str(found).replace("·", "") == str(expected).replace("·", "")


def _ask_has(slot: str, result: spans.SpanResult) -> bool:
    spans_ = result.spans
    if slot == "universe":
        return not any(s.kind == "instrument" for s in spans_)
    if slot == "exit":
        return any(s.kind == "action" and s.meaning.get("side") == "entry" for s in spans_) and not any(
            s.kind == "action" and s.meaning.get("side") == "exit" for s in spans_
        )
    if slot == "ma_window":
        return any(s.entry_id == "ma_bare" for s in spans_)
    if slot == "window_not_allowed":
        return any(s.kind == "indicator" and s.meaning.get("window") not in (None, 5, 10, 20, 60, 120, 14, 1)
                   for s in spans_)
    if slot == "rebalance_not_allowed":
        return any(s.kind == "rebalance" and s.meaning.get("not_allowed") for s in spans_)
    if slot == "weights_over_100":
        return sum(s.meaning["value"] for s in spans_ if s.meaning.get("unit") == "percent") > 100
    if slot == "threshold":
        # 지표나 움직임은 있는데 기준 숫자가 없다
        has_number = any(s.kind == "number" for s in spans_)
        return not has_number and any(s.kind in ("indicator", "verb") for s in spans_)
    if slot == "always_both_true":
        return sum(1 for s in spans_ if s.kind == "verb") >= 2
    return False


if __name__ == "__main__":
    raise SystemExit(main())
