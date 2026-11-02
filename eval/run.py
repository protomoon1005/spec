"""평가 (v0.2 판정 혼동행렬 + 응답 시간). core.pipeline.run 하나만 부른다.

  python -m eval.run              # 캐시 없이 실제 LLM 호출 (응답 시간 측정)
  python -m eval.run --use-cache  # 캐시 사용
  python -m eval.run --compare eval/out/verdict_run_step3.jsonl   # 이전 실행 결과와 나란히 비교
  python -m eval.run --use-cache --ab   # 4단계 근거 검사를 켰을 때와 껐을 때 비교 (각각 저장)
  python -m eval.run --no-prices        # 가격 데이터 없이 (2단계 NO_DATA·5단계 시험 실행 안 함)
  python -m eval.run --baseline         # 비교 대상(LLM 코드 직접 생성)과 나란히 → eval/report_baseline.md (eval/baseline_eval.py)

입력: eval/dev_sentences.jsonl (개발용 세트). 결과 원본은 eval/out/verdict_run.jsonl (git 제외).
판정은 규칙(core.decide + 검사 1~5단계)이 정한 최종 판정 하나뿐이다 (LLM 은 판정하지 않는다).
  proceed → ok, clarify → clarify, reject → reject, error → error
첫 입력 1회 기준 판정이다 (되묻기 답변은 넣지 않는다).
"틀린 전략이 통과" = 정답이 ok 가 아닌데 최종 판정이 ok(=백테스트로 진행)인 건수.
정답 전략서가 아직 TODO 라 전략서 내용 일치는 자동으로 세지 못한다 — 통과한 전략서를 함께 출력하니 사람이 본다.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.pipeline import load_prices, run  # noqa: E402
from core.slot_fill import MODEL, OllamaUnavailable, _ollama_chat, ollama_status  # noqa: E402
from core.validate import Stages  # noqa: E402

SENTENCES = ROOT / "eval" / "dev_sentences.jsonl"
OUT = ROOT / "eval" / "out" / "verdict_run.jsonl"
OUT_NO4 = ROOT / "eval" / "out" / "verdict_run_no4.jsonl"
LABELS = ["ok", "clarify", "reject"]
TO_LABEL = {"proceed": "ok", "confirm": "ok", "clarify": "clarify", "reject": "reject", "invalid": "invalid",
            "error": "error"}


def load_sentences(path: Path = SENTENCES) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_run(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def warm_up() -> float:
    """모델 로드 시간을 측정에서 빼기 위한 1회 호출."""
    t = time.perf_counter()
    _ollama_chat([{"role": "user", "content": "ok"}], {"type": "object", "properties": {"a": {"type": "string"}},
                                                       "required": ["a"]})
    return time.perf_counter() - t


def final_of(row: dict) -> str:
    """최종 판정. 이전 형식(legacy) 결과는 next_step 으로 계산한다."""
    return TO_LABEL[row.get("verdict") or row["next_step"]]


def confusion(rows: list[dict], gold: dict[str, str], pred_fn, cols: list[str]) -> str:
    lines = ["| 정답 \\ 예측 | " + " | ".join(cols) + " | 정확도 |", "|" + "---|" * (len(cols) + 2)]
    for g in LABELS:
        sub = [r for r in rows if gold[r["id"]] == g]
        counts = [sum(1 for r in sub if pred_fn(r) == c) for c in cols]
        acc = sum(pred_fn(r) == g for r in sub) / len(sub) if sub else 0
        lines.append(f"| {g} ({len(sub)}) | " + " | ".join(map(str, counts)) + f" | {acc:.0%} |")
    total = sum(gold[r["id"]] == pred_fn(r) for r in rows)
    lines.append(f"\n정확도: {total}/{len(rows)} = {total / len(rows):.1%}")
    return "\n".join(lines)


def passed_wrong(rows: list[dict], gold: dict[str, str]) -> list[dict]:
    return [r for r in rows if gold[r["id"]] != "ok" and final_of(r) == "ok"]


def spec_summary(spec: dict | None) -> str:
    if not spec:
        return "(없음)"
    uni = ",".join(f"{x['name']}:{'1/N' if x['weight'] is None else format(x['weight'], 'g')}" for x in spec["assets"])
    r = spec.get("rules") or {}
    j = lambda x: json.dumps(x, ensure_ascii=False) if x else "-"   # noqa: E731
    bt = spec["backtest"]
    return (f"[{uni}] 매수: {j(r.get('entry'))} | 매도: {j(r.get('exit'))} | 리밸런싱 {spec.get('rebalance')} | "
            f"{bt['start']}~{bt['end']} {bt['initial_cash']:g} | 기본값 {spec.get('defaults_applied')}")


def report(rows: list[dict], gold: dict[str, str], title: str) -> None:
    print(f"\n## {title}\n")
    print(confusion(rows, gold, final_of, LABELS + ["invalid", "error"]))
    pw = passed_wrong(rows, gold)
    print(f"\n틀린 전략이 통과(정답≠ok 인데 최종 ok): {len(pw)}건 {[r['id'] for r in pw]}")


def compare(prev: list[dict], cur: list[dict], gold: dict[str, str]) -> None:
    p = {r["id"]: r for r in prev}
    print("\n## 문장별 비교 (이전 → 현재)\n")
    print("| id | 정답 | 이전 최종 | 현재 최종 | 문장 |")
    print("|---|---|---|---|---|")
    for r in cur:
        o = p.get(r["id"])
        if o is None:
            continue
        mark = lambda v: v if v == gold[r["id"]] else f"**{v}**"   # noqa: E731 — 오답 굵게
        print(f"| {r['id']} | {gold[r['id']]} | {mark(final_of(o))} | {mark(final_of(r))} | {r['text']} |")


def run_all(sentences: list[dict], use_cache: bool, stages: Stages, prices, label: str) -> list[dict]:
    print(f"\n## 실행: {label}\n")
    rows = []
    for s in sentences:
        r = run(s["text"], use_cache=use_cache, stages=stages, prices=prices)
        f = r.fill
        retry = r.retry or {}
        row = {"id": s["id"], "text": s["text"], "gold": s["verdict"], "verdict": r.verdict,
               "questions": [q.to_dict() for q in r.questions],
               "rejection": r.rejection.to_dict() if r.rejection else None,
               "spec": r.spec if r.verdict == "proceed" else None,
               "issues": [f"{i} ({i.kind})" for i in (r.report.issues if r.report else [])],
               "stage4_first": [str(i) for i in retry.get("issues", [])],     # 첫 시도에서 4단계가 잡은 것
               "stage4_first_spec": retry.get("first_spec"),
               "stage4_kept_first": retry.get("kept_first"),   # 다시 채우기가 더 나빠져 처음 결과를 유지한 이유
               "op_log": r.op_log,
               "elapsed_s": round(f.elapsed_s, 2), "attempts": f.attempts, "cached": f.cached,
               "error": r.error, "raw": f.raw, "meta": f.meta}
        rows.append(row)
        mark = "✓" if row["gold"] == final_of(row) else "✗"
        why = (r.rejection.code if r.rejection else "") + " ".join(q.id for q in r.questions)
        print(f"{mark} {s['id']:11s} {row['elapsed_s']:6.1f}s 정답={row['gold']:7s} 최종={final_of(row):8s} "
              f"{'[4단계]' if retry else '':7s} {why[:70]:70s} {s['text']}", flush=True)
    return rows


def save(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fp:
        for row in rows:
            fp.write(json.dumps(row, ensure_ascii=False) + "\n")


def ab_report(on: list[dict], off: list[dict], gold: dict[str, str]) -> None:
    """4단계 근거 검사를 켰을 때와 껐을 때."""
    p = {r["id"]: r for r in off}

    def acc(rows):
        return sum(final_of(r) == gold[r["id"]] for r in rows)
    print("\n## 4단계 근거 검사 켜기 / 끄기\n")
    print("| | 4단계 끔 | 4단계 켬 |\n|---|---|---|")
    print(f"| 정확도 | {acc(off)}/{len(off)} | {acc(on)}/{len(on)} |")
    print(f"| 틀린 진행 (정답≠ok 인데 최종 ok) | {len(passed_wrong(off, gold))} | {len(passed_wrong(on, gold))} |")
    print(f"| 진행(ok) 건수 | {sum(final_of(r) == 'ok' for r in off)} | {sum(final_of(r) == 'ok' for r in on)} |")
    caught = [r for r in on if r["stage4_first"]]
    print(f"\n4단계가 첫 시도에서 잡은 문장: {len(caught)}건 (→ LLM 1회 다시 채움)")
    for r in caught:
        o = p[r["id"]]
        print(f"- {r['id']} 정답={gold[r['id']]} 끔={final_of(o)} → 켬={final_of(r)} | {r['text']}")
        for x in r["stage4_first"]:
            print(f"    · {x}")
        if r.get("stage4_kept_first"):
            print(f"    → 다시 채운 결과를 버리고 처음 결과 유지: {r['stage4_kept_first']}")
        if r["verdict"] == "clarify":
            print(f"    → 다시 채운 뒤에도 걸려 되묻기: {[q['text'] for q in r['questions']]}")
    changed = [r["id"] for r in on if final_of(r) != final_of(p[r["id"]])]
    print(f"\n최종 판정이 바뀐 문장: {len(changed)}건 {changed}")
    stop = [(r["id"], gold[r["id"]]) for r in on if final_of(p[r["id"]]) == "ok" and final_of(r) != "ok"]
    print(f"끄면 진행되는데 켜면 멈춘 문장(잡아낸 건수): {len(stop)}건 {stop}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--use-cache", action="store_true")
    ap.add_argument("--compare", type=Path, help="이전 실행 결과 jsonl (같은 id 로 나란히 비교, 정답은 현재 라벨)")
    ap.add_argument("--ab", action="store_true", help="4단계 근거 검사를 켰을 때와 껐을 때 둘 다 돌려 비교")
    ap.add_argument("--no-prices", action="store_true", help="가격 데이터 없이 (NO_DATA·시험 실행 안 함)")
    ap.add_argument("--baseline", action="store_true",
                    help="LLM 코드 직접 생성과 나란히 비교 (각 3회, 캐시 없이) → eval/report_baseline.md")
    ap.add_argument("--baseline-rereport", action="store_true",
                    help="LLM 을 부르지 않고 저장된 eval/out/baseline_run.jsonl 로 보고서만 다시 쓴다")
    args = ap.parse_args(argv)
    if args.baseline_rereport:
        from eval import baseline_eval
        return baseline_eval.rereport()

    ok, msg = ollama_status()
    if not ok:
        print(msg)
        return 1
    if not args.use_cache or args.baseline:
        print(f"모델 워밍업(로드) {warm_up():.1f}s — 아래 응답 시간에서 제외")

    sentences = load_sentences()
    if args.baseline:
        from eval import baseline_eval
        try:
            return baseline_eval.main(sentences)
        except OllamaUnavailable as e:
            print(e)
            return 1
    gold = {s["id"]: s["verdict"] for s in sentences}
    prices = None if args.no_prices else load_prices
    off = None
    try:
        rows = run_all(sentences, args.use_cache, Stages.ALL, prices, "검사 1~5단계")
        if args.ab:
            off = run_all(sentences, True, Stages.ALL & ~Stages.GROUNDING, prices, "4단계 끔 (빈칸 채우기는 캐시)")
    except OllamaUnavailable as e:
        print(e)
        return 1
    save(rows, OUT)
    if off is not None:
        save(off, OUT_NO4)

    report(rows, gold, f"현재 ({MODEL}, {len(rows)}문장)")
    if args.compare:
        prev = load_run(args.compare)
        report(prev, gold, f"이전 {args.compare.name} (정답은 현재 라벨 기준으로 다시 채점)")
        compare(prev, rows, gold)

    print("\n## 백테스트로 진행된 전략서 (사람이 원문과 대조할 것)\n")
    for r in rows:
        if final_of(r) == "ok":
            print(f"- {r['id']} {r['text']}\n    → {spec_summary(r['spec'])}")

    times = [r["elapsed_s"] for r in rows]
    print(f"\n응답 시간: 평균 {statistics.mean(times):.2f}s · 중앙값 {statistics.median(times):.2f}s · "
          f"최소 {min(times):.2f}s · 최대 {max(times):.2f}s" + (" (캐시 적중 포함)" if args.use_cache else ""))
    print(f"재시도 발생: {sum(r['attempts'] > 1 for r in rows)}건")
    dist = {k: sum(r["verdict"] == k for r in rows) for k in ("proceed", "clarify", "reject", "error")}
    print(f"판정 분포: {dist}")
    ops = [(r["id"], x) for r in rows for x in r["op_log"]]
    print(f"\n연산자: 표현 표 값이 LLM 값과 다른 경우 {len(ops)}건 (표 값을 씀, 기록만)")
    for rid, x in ops:
        print(f"- {rid} {x['evidence']!r}: LLM {x['llm_op']} → 표 {x['table_op']} ({x['source']})")
    if off is not None:
        ab_report(rows, off, gold)
    print(f"원본 저장: {OUT.relative_to(ROOT)}" + (f", {OUT_NO4.relative_to(ROOT)}" if off is not None else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
