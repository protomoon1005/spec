#!/usr/bin/env python3
"""빅카인즈 기사 감성 채점 — 일회성 오프라인 컨테이너에서 돈다(api/worker 이미지에 torch 없음).

범위 · 묶음 매핑 · 모델 · 입력 규칙은 backend/app/views/sentiment/lens.py 가 정본이다.

입력: 빅카인즈 추출 CSV(.csv.gz, 열 id,date,outlet,title,cat1,cat2,cat3,url,body,excluded).
출력: 입력 파일마다 <이름>.scored.csv.gz (열 id,published_at,outlet,title,url,groups,p_neg,p_neu,p_pos,
      model_version). 본문은 출력하지 않는다. 출력이 이미 있으면 건너뛴다(이어 돌리기).
**빅카인즈 원본 · 채점 CSV 는 저장소에 커밋하지 않는다(재배포 제한).** 경로를 인자로 받는다.

재현성: 스레드 1(프로세스 병렬은 --workers), eval · no_grad, 고정 길이 패딩(MAX_LEN), 확률 소수 6자리.
같은 글 = 같은 점수가 되도록 배치 구성이 결과에 끼지 않게 한 것이다.

published_at: 빅카인즈 식별자 '언론사코드.YYYYMMDDHHMMSS+순번' 의 시각을 KST 입력 시각으로 쓴다.

사용 (저장소 루트에서):
    docker build -f scripts/score_news.Dockerfile -t spec-score-news:1 .
    docker run --rm -v "$PWD":/repo:ro -v <입력폴더>:/in:ro -v <출력폴더>:/out -v <HF캐시>:/hf \\
        -e HF_HOME=/hf spec-score-news:1 python /repo/scripts/score_news.py /in/*.csv.gz --out-dir /out \\
        --workers 8
    속도만 재기: --limit 1000 --out-dir /tmp/x
"""
from __future__ import annotations

import argparse
import csv
import gzip
import multiprocessing as mp
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.views.sentiment import lens  # noqa: E402  (stdlib 만 쓰는 파일)

KST = timezone(timedelta(hours=9))
OUT_COLUMNS = ("id", "published_at", "outlet", "title", "url", "groups", "p_neg", "p_neu", "p_pos",
               "model_version")
BATCH = 32

csv.field_size_limit(1 << 30)


def published_at(article_id: str) -> datetime:
    # '02100601.20230115002512002' -> 2023-01-15 00:25:12 KST
    stamp = article_id.split(".", 1)[1][:14]
    return datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=KST)


def selected(rows):
    # 분석제외가 아니고 묶음이 하나라도 있는 기사. (기사, 묶음들) 을 흘린다.
    for row in rows:
        if (row.get("excluded") or "").strip():
            continue
        categories = [row.get(k) or "" for k in ("cat1", "cat2", "cat3")]
        groups = lens.groups_for(categories, title=row.get("title") or "")
        if groups:
            yield row, groups


def read_selected(path: Path, limit: int | None) -> list[tuple[dict, tuple[str, ...]]]:
    out = []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        for row, groups in selected(csv.DictReader(handle)):
            out.append((row, groups))
            if limit is not None and len(out) >= limit:
                break
    out.sort(key=lambda item: item[0]["id"])
    return out


_MODEL = None


def _init_worker() -> None:
    global _MODEL
    import torch  # noqa: PLC0415
    from transformers import AutoModelForSequenceClassification, AutoTokenizer  # noqa: PLC0415

    torch.set_num_threads(1)
    tokenizer = AutoTokenizer.from_pretrained(lens.MODEL_NAME, revision=lens.MODEL_REVISION)
    model = AutoModelForSequenceClassification.from_pretrained(lens.MODEL_NAME, revision=lens.MODEL_REVISION)
    model.eval()
    labels = {name.lower(): int(i) for i, name in model.config.id2label.items()}
    index = {k: labels[k] for k in ("negative", "neutral", "positive")}
    _MODEL = (torch, tokenizer, model, index)


def _score_batch(texts: list[str]) -> list[tuple[float, float, float]]:
    torch, tokenizer, model, index = _MODEL
    encoded = tokenizer(texts, padding="max_length", truncation=True, max_length=lens.MAX_LEN,
                        return_tensors="pt")
    with torch.no_grad():
        probs = torch.softmax(model(**encoded).logits, dim=-1).tolist()
    return [tuple(round(p[index[k]], 6) for k in ("negative", "neutral", "positive")) for p in probs]


def score_file(path: Path, out_dir: Path, *, workers: int, limit: int | None) -> tuple[int, float]:
    target = out_dir / (path.name.removesuffix(".csv.gz") + ".scored.csv.gz")
    if target.exists():
        print(f"[score_news] {target.name} 있음 — 건너뜀")
        return 0, 0.0
    items = read_selected(path, limit)
    texts = [lens.score_text(row["title"], row.get("body") or "") for row, _ in items]
    batches = [texts[i : i + BATCH] for i in range(0, len(texts), BATCH)]
    started = time.monotonic()
    with mp.get_context("spawn").Pool(workers, initializer=_init_worker) as pool:
        load_started = time.monotonic()
        scored = [p for batch in pool.imap(_score_batch, batches) for p in batch]
    elapsed = time.monotonic() - load_started
    partial = target.with_suffix(".tmp")
    with gzip.open(partial, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(OUT_COLUMNS)
        for (row, groups), (p_neg, p_neu, p_pos) in zip(items, scored, strict=True):
            writer.writerow([row["id"], published_at(row["id"]).isoformat(), row["outlet"], row["title"],
                             row.get("url") or "", ";".join(groups), p_neg, p_neu, p_pos, lens.MODEL_VERSION])
    os.replace(partial, target)
    rate = len(items) / elapsed if elapsed else 0.0
    print(f"[score_news] {path.name}: {len(items)}건 {elapsed:.1f}s ({rate:.1f}건/s, workers={workers}, "
          f"총 {time.monotonic() - started:.1f}s) -> {target.name}")
    return len(items), elapsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--limit", type=int, default=None, help="파일마다 앞 N건만(속도 측정용)")
    parser.add_argument("--count-only", action="store_true", help="채점 대상 건수만 센다")
    args = parser.parse_args(argv)
    if args.count_only:
        total = 0
        for path in args.inputs:
            with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
                n = sum(1 for _ in selected(csv.DictReader(handle)))
            print(f"[score_news] {path.name}: 대상 {n}건")
            total += n
        print(f"[score_news] 합계 {total}건")
        return 0
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for path in args.inputs:
        score_file(path, args.out_dir, workers=args.workers, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
