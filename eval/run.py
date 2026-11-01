"""평가 (3단계 범위: verdict 혼동행렬 + 응답 시간). 5단계에서 baseline·반복 일치율이 추가된다.

  python -m eval.run              # 캐시 없이 실제 LLM 호출 (응답 시간 측정)
  python -m eval.run --use-cache  # 캐시 사용
  python -m eval.run --compare eval/out/verdict_run_step3.jsonl   # 이전 실행 결과와 나란히 비교

입력: eval/dev_sentences.jsonl (개발용 세트 — 프롬프트·사전 조정에 쓴다. 최종 평가용 세트는 따로 만든다)
결과 원본은 eval/out/verdict_run.jsonl 에 저장한다 (git 제외).

판정은 두 가지를 따로 센다.
  LLM 판정  : LLM 이 출력한 verdict
  최종 판정 : 검증기(core.validate)가 확정한 verdict (ok | clarify | reject | invalid) — 화면이 실제로 가는 곳
"틀린 전략이 검증을 통과" = 정답이 ok 가 아닌데 최종 판정이 ok(=백테스트로 진행)인 건수.
정답 명세가 아직 TODO 라 정답이 ok 인 문장의 명세 내용 일치는 자동으로 세지 못한다 — 통과한 명세를 함께 출력하니 사람이 본다.
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

from core.specify import MODEL, OllamaUnavailable, _ollama_chat, ollama_status, specify  # noqa: E402

SENTENCES = ROOT / "eval" / "dev_sentences.jsonl"
OUT = ROOT / "eval" / "out" / "verdict_run.jsonl"
LABELS = ["ok", "clarify", "reject"]
STEP_TO_VERDICT = {"confirm": "ok", "clarify": "clarify", "reject": "reject", "invalid": "invalid", "error": "error"}


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
    """최종 판정. 이전 형식(3단계) 결과는 next_step 으로 계산한다."""
    return STEP_TO_VERDICT[row["next_step"]]


def llm_of(row: dict) -> str:
    return row.get("llm_verdict", row.get("pred")) or "(없음)"


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
    """정답이 ok 가 아닌데 백테스트로 진행된 문장."""
    return [r for r in rows if gold[r["id"]] != "ok" and final_of(r) == "ok"]


def spec_summary(raw: str | None) -> str:
    try:
        d = json.loads(raw or "")
    except json.JSONDecodeError:
        return "(JSON 아님)"
    uni = ",".join(f"{u['name']}:{u['weight']:g}" for u in d.get("universe") or [])

    def conds(rule):
        if not rule:
            return "-"
        cs = [f"{c['left']} {c['op']} {json.dumps(c['right'], ensure_ascii=False)}" for c in rule.get("conditions", [])]
        extra = [f"{k}={rule[k]}" for k in ("stop_loss", "take_profit", "max_hold_days") if rule.get(k) is not None]
        return " / ".join(cs + extra) or "-"
    bt = d.get("backtest") or {}
    return (f"[{uni}] 매수: {conds(d.get('entry'))} | 매도: {conds(d.get('exit'))} | "
            f"{bt.get('start')}~{bt.get('end')} {bt.get('initial_cash')}")


def report(rows: list[dict], gold: dict[str, str], title: str) -> None:
    print(f"\n## {title}\n")
    print("### LLM 판정\n")
    print(confusion(rows, gold, llm_of, LABELS + ["(없음)"]))
    print("\n### 최종 판정 (검증기 확정, 화면이 실제로 가는 곳)\n")
    print(confusion(rows, gold, final_of, LABELS + ["invalid", "error"]))
    pw = passed_wrong(rows, gold)
    print(f"\n틀린 전략이 검증을 통과(정답≠ok 인데 최종 ok): {len(pw)}건 {[r['id'] for r in pw]}")


def compare(prev: list[dict], cur: list[dict], gold: dict[str, str]) -> None:
    p = {r["id"]: r for r in prev}
    print("\n## 문장별 비교 (이전 → 현재)\n")
    print("| id | 정답 | 이전 LLM | 이전 최종 | 현재 LLM | 현재 최종 | 문장 |")
    print("|---|---|---|---|---|---|---|")
    for r in cur:
        o = p.get(r["id"])
        if o is None:
            continue
        mark = lambda v: v if v == gold[r["id"]] else f"**{v}**"   # noqa: E731 — 오답 굵게
        print(f"| {r['id']} | {gold[r['id']]} | {mark(llm_of(o))} | {mark(final_of(o))} | "
              f"{mark(llm_of(r))} | {mark(final_of(r))} | {r['text']} |")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--use-cache", action="store_true")
    ap.add_argument("--compare", type=Path, help="이전 실행 결과 jsonl (같은 id 로 나란히 비교, 정답은 현재 라벨)")
    args = ap.parse_args(argv)

    ok, msg = ollama_status()
    if not ok:
        print(msg)
        return 1
    if not args.use_cache:
        print(f"모델 워밍업(로드) {warm_up():.1f}s — 아래 응답 시간에서 제외")

    sentences = load_sentences()
    gold = {s["id"]: s["verdict"] for s in sentences}
    rows = []
    for s in sentences:
        try:
            r = specify(s["text"], use_cache=args.use_cache)
        except OllamaUnavailable as e:
            print(e)
            return 1
        rep = r.report
        row = {"id": s["id"], "text": s["text"], "gold": s["verdict"], "llm_verdict": r.llm_verdict,
               "pred": r.verdict, "next_step": r.next_step, "overridden": bool(rep and rep.overridden),
               "questions": rep.questions if rep else [],
               "reject_reason": rep.reject_reason.model_dump() if rep and rep.reject_reason else None,
               "elapsed_s": round(r.elapsed_s, 2), "attempts": r.attempts, "cached": r.cached,
               "issues": [f"{i} ({i.kind})" for i in (rep.issues if rep else [])], "raw": r.raw, "meta": r.meta}
        rows.append(row)
        mark = "✓" if row["gold"] == final_of(row) else "✗"
        print(f"{mark} {s['id']:11s} {row['elapsed_s']:6.1f}s 정답={row['gold']:7s} LLM={str(row['llm_verdict']):7s} "
              f"최종={final_of(row):8s} {s['text']}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    report(rows, gold, f"현재 ({MODEL}, {len(rows)}문장)")
    if args.compare:
        prev = load_run(args.compare)
        report(prev, gold, f"이전 {args.compare.name} (정답은 현재 라벨 기준으로 다시 채점)")
        compare(prev, rows, gold)

    print("\n## 백테스트로 진행된 명세 (사람이 원문과 대조할 것)\n")
    for r in rows:
        if final_of(r) == "ok":
            print(f"- {r['id']} {r['text']}\n    → {spec_summary(r['raw'])}")

    times = [r["elapsed_s"] for r in rows]
    print(f"\n응답 시간: 평균 {statistics.mean(times):.2f}s · 중앙값 {statistics.median(times):.2f}s · "
          f"최소 {min(times):.2f}s · 최대 {max(times):.2f}s" + (" (캐시 적중 포함)" if args.use_cache else ""))
    print(f"재시도 발생: {sum(r['attempts'] > 1 for r in rows)}건 · 검증기가 LLM 판정을 바꾼 건수: "
          f"{sum(r['overridden'] for r in rows)}건")
    steps = {k: sum(r["next_step"] == k for r in rows) for k in ("confirm", "clarify", "reject", "invalid", "error")}
    print(f"다음 단계 분포: {steps}")
    print(f"원본 저장: {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
