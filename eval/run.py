"""평가 (3단계 범위: verdict 혼동행렬 + 응답 시간). 5단계에서 baseline·반복 일치율이 추가된다.

  python -m eval.run              # 캐시 없이 실제 LLM 호출 (응답 시간 측정)
  python -m eval.run --use-cache  # 캐시 사용

결과 원본은 eval/out/verdict_run.jsonl 에 저장한다 (git 제외).
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

SENTENCES = ROOT / "eval" / "sentences.jsonl"
OUT = ROOT / "eval" / "out" / "verdict_run.jsonl"
LABELS = ["ok", "clarify", "reject"]


def load_sentences(path: Path = SENTENCES) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def warm_up() -> float:
    """모델 로드 시간을 측정에서 빼기 위한 1회 호출."""
    t = time.perf_counter()
    _ollama_chat([{"role": "user", "content": "ok"}], {"type": "object", "properties": {"a": {"type": "string"}},
                                                       "required": ["a"]})
    return time.perf_counter() - t


def confusion(rows: list[dict]) -> str:
    cols = LABELS + ["(없음)"]
    lines = ["| 정답 \\ 예측 | " + " | ".join(cols) + " | 정확도 |", "|" + "---|" * (len(cols) + 2)]
    for g in LABELS:
        sub = [r for r in rows if r["gold"] == g]
        counts = [sum(1 for r in sub if (r["pred"] or "(없음)") == c) for c in cols]
        acc = counts[LABELS.index(g)] / len(sub) if sub else 0
        lines.append(f"| {g} ({len(sub)}) | " + " | ".join(map(str, counts)) + f" | {acc:.0%} |")
    total = sum(r["gold"] == r["pred"] for r in rows)
    lines.append(f"\n전체 verdict 정확도: {total}/{len(rows)} = {total / len(rows):.1%}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--use-cache", action="store_true")
    args = ap.parse_args(argv)

    ok, msg = ollama_status()
    if not ok:
        print(msg)
        return 1
    if not args.use_cache:
        print(f"모델 워밍업(로드) {warm_up():.1f}s — 아래 응답 시간에서 제외")

    rows = []
    for s in load_sentences():
        try:
            r = specify(s["text"], use_cache=args.use_cache)
        except OllamaUnavailable as e:
            print(e)
            return 1
        row = {"id": s["id"], "text": s["text"], "gold": s["verdict"], "pred": r.verdict,
               "next_step": r.next_step, "elapsed_s": round(r.elapsed_s, 2), "attempts": r.attempts,
               "cached": r.cached, "issues": [str(i) for i in (r.report.issues if r.report else [])],
               "raw": r.raw, "meta": r.meta}
        rows.append(row)
        mark = "✓" if row["gold"] == row["pred"] else "✗"
        print(f"{mark} {s['id']:11s} {row['elapsed_s']:6.1f}s 정답={row['gold']:7s} 예측={str(row['pred']):7s} "
              f"다음={row['next_step']:8s} {s['text']}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    times = [r["elapsed_s"] for r in rows]
    print(f"\n## verdict 혼동행렬 ({MODEL}, {len(rows)}문장)\n")
    print(confusion(rows))
    print(f"\n응답 시간: 평균 {statistics.mean(times):.2f}s · 중앙값 {statistics.median(times):.2f}s · "
          f"최소 {min(times):.2f}s · 최대 {max(times):.2f}s" + (" (캐시 적중 포함)" if args.use_cache else ""))
    print(f"재시도 발생: {sum(r['attempts'] > 1 for r in rows)}건")
    steps = {k: sum(r["next_step"] == k for r in rows) for k in ("confirm", "clarify", "reject", "invalid", "error")}
    print(f"다음 단계 분포: {steps}")
    print(f"원본 저장: {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
