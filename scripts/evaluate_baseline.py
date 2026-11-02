#!/usr/bin/env python3
"""비교 대상(LLM 이 코드를 직접 짜는 방식)을 평가 데이터셋으로 재고, 우리 시스템과 나란히 보고한다.

docs/v0.2-plan.md 9단계 c, 설계 문서 "비교 대상".

  문장마다
    1 우리 종목 이름 찾기(app/m1/spans.py)로 문장에 나온 종목을 찾고 그 종목의 가격만 넘긴다(A안)
    2 LLM 이 strategy(prices, start, end) 함수를 짜거나 NEED_CLARIFICATION · CANNOT_IMPLEMENT 로 답한다
    3 코드는 샌드박스(app/baseline/sandbox.py)에서 돌려 주문을 받고, 우리 엔진과 같은 비용 · 세금으로 체결한다
  판정       코드를 냈으면 ok, 되물으면 ask, 구현 못 한다면 reject. 응답을 해석하지 못하면 오답
  변환       정답이 ok 이고 정답 전략서를 데모에서 돌릴 수 있는 문장만 센다(나머지는 n/a).
             정답 체결과 비교 대상 체결이 같으면 맞음.
             같다 = 체결일 · 종목 · 방향이 같고 목표 비중 차이 0.5%p 이내
             코드가 실패하면(정적 검사 · 예외 · 타임아웃 · 출력 형식) 틀림
  되묻기     정답이 ask 이고 비교 대상이 되물었는지(되물을 거리까지는 보지 않는다. 자유 질문이라 맞출 수 없다)
  거절       정답이 reject 이고 비교 대상이 구현할 수 없다고 했는지(사유 코드는 보지 않는다)
  우리 시스템 쪽 숫자는 scripts/evaluate_v0_2.py 를 같은 문장에 돌려 얻는다(AI 응답 캐시를 같이 쓴다)

개발용(dev)만 돈다. test 는 --final 을 붙여야 돈다.
LLM 응답 캐시는 /tmp/baseline-cache 에 둔다. 처음 돌릴 때 문장마다 수십 초가 걸린다.

실행 (api 컨테이너, Ollama · 종목 원장 · 가격이 든 DB 필요):
    docker compose exec api python /repo/scripts/evaluate_baseline.py --json /tmp/baseline_dev.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "scripts"))

from evaluate_v0_2 import evaluate as evaluate_ours  # noqa: E402
from measure_pairing import CachedClient  # noqa: E402

from app.backtest import rules_engine  # noqa: E402
from app.backtest.rules_service import run_rules_backtest  # noqa: E402
from app.baseline import codegen, compare, orders, sandbox  # noqa: E402
from app.contracts.strategy_v0_2 import StrategyV0_2  # noqa: E402
from app.llm.ollama_client import OllamaClient  # noqa: E402
from app.m1 import compile_v0_2  # noqa: E402
from app.repositories import etf_master  # noqa: E402

DATASET = REPO / "eval" / "dataset.jsonl"
CACHE = Path("/tmp/baseline-cache")
DEFAULT_START, DEFAULT_END, DEFAULT_CAPITAL = date(2023, 1, 1), date(2025, 12, 31), 10_000_000
KIND_TO_TYPE = {"code": "ok", "clarify": "ask", "reject": "reject", "error": "error"}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    parser.add_argument("--final", action="store_true", help="test 를 돌릴 때만. 마지막 한 번")
    parser.add_argument("--json", type=Path, help="문장별 결과를 JSON 으로 남길 곳")
    parser.add_argument("--only", nargs="*", help="이 id 만 돈다(확인용)")
    args = parser.parse_args()
    if args.split == "test" and not args.final:
        print("test 는 마지막 평가에서 한 번만 쓴다. 정말이면 --final 을 붙인다", file=sys.stderr)
        return 2

    rows = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = [r for r in rows if r["split"] == args.split and (not args.only or r["id"] in args.only)]
    inner = OllamaClient()
    ours_client = CachedClient(inner)
    records = []
    for i, row in enumerate(rows, 1):
        rec = evaluate_baseline(row, inner)
        rec["ours"] = evaluate_ours(row, ours_client)
        records.append(rec)
        print(
            f"[{i}/{len(rows)}] {row['id']} 정답 {row['type']} 비교 대상 {rec['got']} "
            f"{'맞음' if rec['good'] else '틀림' if rec['good'] is False else 'n/a'} "
            f"({rec['elapsed_s']:.0f}초{' 캐시' if rec['cached'] else ''})",
            flush=True,
        )
    report(records)
    if args.json:
        args.json.write_text(json.dumps(records, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return 0


def evaluate_baseline(row: dict, client) -> dict:
    expected = StrategyV0_2.model_validate(row["expected_spec"]) if row["type"] == "ok" else None
    start = expected.backtest.start if expected else DEFAULT_START
    end = expected.backtest.end if expected else DEFAULT_END
    capital = float(expected.backtest.capital) if expected else DEFAULT_CAPITAL

    tickers = sorted({s.meaning["ticker"] for s in compile_v0_2.find(row["text"]).by_kind("instrument")})
    records = etf_master.get_by_tickers(tickers)
    tickers = [t for t in tickers if t in records]
    frames = compare.frames(tickers, max(end, DEFAULT_END))
    tickers = [t for t in tickers if t in frames]
    listing = [(t, records[t].name, "ETF" if records[t].kind == "etf" else "주식") for t in tickers]

    prompt = codegen.build_prompt(listing, DEFAULT_START.isoformat(), DEFAULT_END.isoformat())
    gen = codegen.generate(row["text"], prompt, client, cache_dir=CACHE)
    rec = {
        "id": row["id"],
        "text": row["text"],
        "expected": row["type"],
        "got": KIND_TO_TYPE[gen.kind],
        "tickers": tickers,
        "message": gen.message,
        "elapsed_s": gen.elapsed_s,
        "cached": gen.cached,
        "good": None,
    }
    if row["type"] == "ask":
        rec["good"] = gen.kind == "clarify"
        return rec
    if row["type"] == "reject":
        rec["good"] = gen.kind == "reject"
        return rec

    try:
        want = run_rules_backtest(expected).fills
    except (rules_engine.UnsupportedStrategy, ValueError) as exc:
        rec["why_na"] = f"정답을 데모에서 돌릴 수 없음: {exc}"
        return rec  # n/a
    rec["want_fills"] = len(want)
    if gen.kind != "code":
        rec["good"] = False
        return rec
    run = sandbox.run_code(gen.code, frames, start.isoformat(), end.isoformat(), known=set(tickers))
    if not run.ok:
        rec.update(good=False, run_error=f"{run.stage}: {run.error}")
        return rec
    categories = {t: compare.tax_category(records[t]) for t in tickers}
    clean = [{**o, "weight": min(max(float(o["weight"]), 0.0), 1.0)} for o in run.orders]
    got = orders.execute_orders(clean, frames, start, end, capital, categories)
    rec.update(good=orders.same_fills(want, got), orders=len(run.orders), got_fills=len(got))
    return rec


def report(records: list[dict]) -> None:
    total = len(records)
    print(f"\n문장 {total}건 (왼쪽 우리 시스템, 오른쪽 비교 대상)\n")
    labels = ("ok", "ask", "reject", "error")
    for who, get in (("우리 시스템", lambda r: r["ours"]["got"]), ("비교 대상", lambda r: r["got"])):
        confusion = Counter((r["expected"], get(r)) for r in records)
        print(f"{who} 판정 혼동표 (줄: 정답, 칸: 결과)")
        print("        " + "".join(f"{k:>8}" for k in labels))
        for want in labels[:3]:
            print(f"{want:>8}" + "".join(f"{confusion[(want, got)]:>8}" for got in labels))
        print()

    ours_type = sum(r["ours"]["got"] == r["expected"] for r in records)
    base_type = sum(r["got"] == r["expected"] for r in records)
    print(f"판정 정확도          {ours_type}/{total}  대  {base_type}/{total}")

    ok = [r for r in records if r["expected"] == "ok"]
    runnable = [r for r in ok if r["good"] is not None]
    ours_conv = sum(bool(r["ours"].get("converted")) for r in runnable)
    base_conv = sum(bool(r["good"]) for r in runnable)
    print(
        f"변환(주문 비교)      {ours_conv}/{len(runnable)}  대  {base_conv}/{len(runnable)}"
        f"   (정답을 데모에서 못 돌리는 {len(ok) - len(runnable)}건은 n/a)"
    )
    asks = [r for r in records if r["expected"] == "ask"]
    print(
        f"되묻기               {sum(bool(r['ours'].get('ask_ok')) for r in asks)}/{len(asks)}  대  "
        f"{sum(bool(r['good']) for r in asks)}/{len(asks)}   (비교 대상은 되물었는지만 본다)"
    )
    rejects = [r for r in records if r["expected"] == "reject"]
    print(
        f"거절                 {sum(bool(r['ours'].get('reject_ok')) for r in rejects)}/{len(rejects)}  대  "
        f"{sum(bool(r['good']) for r in rejects)}/{len(rejects)}   (비교 대상은 사유 코드를 보지 않는다)"
    )
    failed = [r for r in ok if r.get("run_error")]
    print(f"비교 대상 코드 실행 실패 {len(failed)}건")
    for r in failed:
        print(f"  {r['id']} {r['run_error'][:150]}")

    print("\n비교 대상이 틀린 문장")
    for r in records:
        if r["good"] is False:
            detail = (
                r.get("run_error")
                or r.get("message")
                or (
                    f"체결 {r.get('got_fills')}건 대 정답 {r.get('want_fills')}건" if "got_fills" in r else ""
                )
            )
            print(f"  {r['id']} 정답 {r['expected']} 결과 {r['got']} | {detail[:150]}")
            print(f"     {r['text']}")


if __name__ == "__main__":
    raise SystemExit(main())
