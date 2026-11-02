#!/usr/bin/env python3
"""전략서 v0.2 명세화 · 판정을 평가 데이터셋으로 잰다 (docs/v0.2-plan.md 9단계, eval/README.md).

  판정       바로 진행(ok) · 되묻기(ask) · 거절(reject) 3분류가 맞는지, 혼동표
  변환       정답이 ok 인 문장: 정답 전략서와 시스템 전략서를 같은 엔진으로 백테스트해 주문이 같은지.
             주문이 같다 = 체결일 · 종목 · 방향이 같고 목표 비중 차이가 0.5%p 이내(eval/README.md).
             데모에서 돌릴 수 없는 정답(업종 고르기, 지수 · VIX 조건)은 전략서가 같은지로 대신 보고 따로 센다
  되묻기     정답이 ask 인 문장: 시스템이 ask 이고,
             정답의 되물을 거리가 시스템이 찾은 되물을 거리 전체 안에 있는지
  거절       정답이 reject 인 문장: 시스템이 reject 이고 사유 코드가 같은지

개발용(dev)만 돈다. 최종 평가용(test)은 --final 을 붙여야 돈다. test 는 마지막에 한 번만 쓴다(eval/README.md).
AI 응답은 scripts/measure_pairing.py 와 같은 캐시(/tmp/pairing-cache)를 쓴다.

실행 (api 컨테이너, Ollama · 종목 원장 · 가격이 든 DB 필요):
    docker compose exec api python /repo/scripts/evaluate_v0_2.py
    docker compose exec api python /repo/scripts/evaluate_v0_2.py --json /tmp/eval_dev.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "scripts"))

from measure_pairing import CachedClient  # noqa: E402

from app.backtest import rules_engine  # noqa: E402
from app.backtest.rules_service import run_rules_backtest  # noqa: E402
from app.contracts.strategy_v0_2 import StrategyV0_2  # noqa: E402
from app.llm.ollama_client import OllamaClient  # noqa: E402
from app.m1 import compile_v0_2, decide, pairing  # noqa: E402

DATASET = REPO / "eval" / "dataset.jsonl"
WEIGHT_TOLERANCE = 0.005


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    parser.add_argument("--final", action="store_true", help="test 를 돌릴 때만. 마지막 한 번")
    parser.add_argument("--json", type=Path, help="문장별 결과를 JSON 으로 남길 곳")
    args = parser.parse_args()
    if args.split == "test" and not args.final:
        print("test 는 마지막 평가에서 한 번만 쓴다. 정말이면 --final 을 붙인다", file=sys.stderr)
        return 2

    rows = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = [r for r in rows if r["split"] == args.split]
    client = CachedClient(OllamaClient())
    records = [evaluate(row, client) for row in rows]
    report(records)
    if args.json:
        args.json.write_text(json.dumps(records, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return 0


def evaluate(row: dict, client) -> dict:
    result = compile_v0_2.find(row["text"])
    early = decide.precheck(result)
    asks: list[str] = []
    spec = None
    if early is not None:
        status, code = "reject", early.code
    else:
        outcome = pairing.pair(result, client)
        judged = compile_v0_2.judge(result, outcome.pairing, overrides={}, rounds_asked=0)
        status = judged.decision.status
        code = judged.decision.rejection.code if status == "reject" else None
        asks = sorted({a.kind for a in judged.decision.all_asks})
        spec = judged.built.spec if status == "ok" else None

    record = {
        "id": row["id"],
        "text": row["text"],
        "expected": row["type"],
        "got": status,
        "code": code,
        "asks": asks,
    }
    if row["type"] == "ok":
        record.update(compare_specs(row["expected_spec"], spec) if status == "ok" else {"converted": False})
    elif row["type"] == "ask":
        want = sorted({a["slot"] for a in row["expected_ask"]})
        record["expected_asks"] = want
        record["ask_ok"] = status == "ask" and set(want) <= set(asks)
    else:
        record["expected_code"] = row["expected_reject"]["code"]
        record["reject_ok"] = status == "reject" and code == row["expected_reject"]["code"]
    return record


def compare_specs(expected: dict, got: StrategyV0_2) -> dict:
    want = StrategyV0_2.model_validate(expected)
    same_spec = _plain(want) == _plain(got)
    try:
        want_orders = _orders(want)
    except (rules_engine.UnsupportedStrategy, ValueError) as exc:
        # 데모에서 돌릴 수 없는 정답(업종 고르기, 지수 · VIX 조건)은 전략서 일치로 대신 본다
        return {"converted": same_spec, "by": "spec", "why_not_orders": str(exc)}
    try:
        got_orders = _orders(got)
    except (rules_engine.UnsupportedStrategy, ValueError) as exc:
        return {"converted": False, "by": "orders", "why_not_orders": f"시스템 전략서를 돌리지 못함: {exc}"}
    return {
        "converted": _same_orders(want_orders, got_orders),
        "by": "orders",
        "same_spec": same_spec,
        "orders": len(want_orders),
    }


def _plain(spec: StrategyV0_2) -> dict:
    data = spec.model_dump(mode="json")
    data.pop("meta", None)
    return data


def _orders(spec: StrategyV0_2) -> list[tuple]:
    result = run_rules_backtest(spec)
    return sorted(
        (str(f["date"]), f["ticker"], f["side"], round(f.get("target_weight", 0.0), 6)) for f in result.fills
    )


def _same_orders(a: list[tuple], b: list[tuple]) -> bool:
    if [x[:3] for x in a] != [x[:3] for x in b]:
        return False
    return all(abs(x[3] - y[3]) <= WEIGHT_TOLERANCE for x, y in zip(a, b, strict=True))


def report(records: list[dict]) -> None:
    labels = ("ok", "ask", "reject")
    confusion = Counter((r["expected"], r["got"]) for r in records)
    total = len(records)
    correct_type = sum(confusion[(k, k)] for k in labels)
    print(f"문장 {total}건\n")
    print("판정 혼동표 (줄: 정답, 칸: 시스템)")
    print("        " + "".join(f"{k:>8}" for k in labels))
    for want in labels:
        print(f"{want:>8}" + "".join(f"{confusion[(want, got)]:>8}" for got in labels))
    print(f"판정 정확도 {correct_type}/{total} = {correct_type / total:.1%}\n")

    ok = [r for r in records if r["expected"] == "ok"]
    by_orders = [r for r in ok if r.get("by") == "orders"]
    by_spec = [r for r in ok if r.get("by") == "spec"]
    converted = sum(r.get("converted", False) for r in ok)
    print(f"변환 정확도(정답 ok) {converted}/{len(ok)} = {converted / max(len(ok), 1):.1%}")
    print(
        f"  주문 비교 {sum(r['converted'] for r in by_orders)}/{len(by_orders)}, "
        f"전략서 비교(데모에서 못 돌리는 정답) {sum(r['converted'] for r in by_spec)}/{len(by_spec)}, "
        f"판정이 ok 가 아님 {sum(1 for r in ok if r['got'] != 'ok')}"
    )
    asks = [r for r in records if r["expected"] == "ask"]
    rejects = [r for r in records if r["expected"] == "reject"]
    print(f"되묻기 재현율 {sum(r['ask_ok'] for r in asks)}/{len(asks)}")
    print(f"거절 정확도 {sum(r['reject_ok'] for r in rejects)}/{len(rejects)}\n")

    print("틀린 문장")
    for r in records:
        good = (
            r.get("converted")
            if r["expected"] == "ok"
            else r.get("ask_ok")
            if r["expected"] == "ask"
            else r.get("reject_ok")
        )
        if not good:
            detail = r.get("why_not_orders") or r.get("expected_asks") or r.get("expected_code") or ""
            print(
                f"  {r['id']} 정답 {r['expected']} 결과 {r['got']} {r['code'] or ''} {r['asks']} | {detail}"
            )
            print(f"     {r['text']}")


if __name__ == "__main__":
    raise SystemExit(main())
