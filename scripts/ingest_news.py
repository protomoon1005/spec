#!/usr/bin/env python3
"""채점된 빅카인즈 기사(scripts/score_news.py 출력)를 기사 · 감성 표에 넣는다. 멱등.

  news_articles : source=언론사, title, body=NULL(본문 저장 안 함), url, published_at=식별자 입력시각(KST),
                  dedup_hash=빅카인즈 식별자. 같은 식별자면 덮어쓴다.
  news_sentiment: 기사 x 묶음마다 한 행. sector=묶음, polarity=p_pos−p_neg, confidence=최대 확률,
                  lens_id · model_version 은 app/views/sentiment/lens.py.
                  같은 기사 · 렌즈의 옛 행은 지우고 넣는다.

url 은 UNIQUE 다. 빅카인즈에는 서로 다른 식별자가 같은 url 을 갖는 일이 있어(2023 파일만 1,784건)
식별자 오름차순으로 처음 나온 기사만 url 을 갖고 나머지는 NULL 로 둔다. DB 에 이미 다른 기사가 가진 url 과
겹치는 경우는 저장소(news.upsert_articles)가 같은 트랜잭션에서 걸러 NULL 로 넣는다 — 연도 파일을 따로
넣거나 다시 넣어도 롤백되지 않는다. 먼저 자리 잡은 기사가 url 을 갖는다.

**채점 CSV 는 저장소에 커밋하지 않는다(재배포 제한).** 경로를 인자로 받는다.

사용 (api 컨테이너 안, 채점 결과 폴더를 마운트하거나 /repo/data/news/ 에 둔다):
    python /repo/scripts/ingest_news.py /repo/data/news/*.scored.csv.gz
"""
from __future__ import annotations

import argparse
import csv
import gzip
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.views.sentiment import lens  # noqa: E402

BATCH = 5000

csv.field_size_limit(1 << 30)


def read_scored(paths: list[Path]) -> list[dict]:
    rows = []
    for path in paths:
        with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
            for record in csv.DictReader(handle):
                if record["model_version"] != lens.MODEL_VERSION:
                    raise ValueError(f"{path.name} {record['id']}: 모델 버전이 다르다 "
                                     f"{record['model_version']!r} != {lens.MODEL_VERSION!r}")
                rows.append(record)
    rows.sort(key=lambda r: r["id"])
    seen = set()
    for row in rows:
        if row["id"] in seen:
            raise ValueError(f"식별자가 겹친다: {row['id']}")
        seen.add(row["id"])
    return rows


def article_rows(records: list[dict]) -> list[dict]:
    used_urls: set[str] = set()
    out = []
    for r in records:
        url = (r["url"] or "").strip() or None
        if url in used_urls:
            url = None
        elif url:
            used_urls.add(url)
        out.append({
            "source": r["outlet"],
            "title": r["title"],
            "url": url,
            "published_at": datetime.fromisoformat(r["published_at"]),
            "dedup_hash": r["id"],
        })
    return out


def sentiment_rows(record: dict, article_id: int) -> list[tuple[int, str, float, float]]:
    p_neg, p_neu, p_pos = (float(record[k]) for k in ("p_neg", "p_neu", "p_pos"))
    polarity = round(p_pos - p_neg, 6)
    confidence = max(p_neg, p_neu, p_pos)
    groups = [g for g in record["groups"].split(";") if g]
    unknown = set(groups) - set(lens.GROUPS)
    if unknown or not groups:
        raise ValueError(f"{record['id']}: 묶음이 잘못됐다 {record['groups']!r}")
    return [(article_id, g, polarity, confidence) for g in groups]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    records = read_scored(args.inputs)
    articles = article_rows(records)
    print(f"[ingest_news] 기사 {len(records)}건 (url 없음 {sum(1 for a in articles if a['url'] is None)}건)")
    if args.dry_run:
        return 0

    from app.repositories import news  # noqa: PLC0415

    total = 0
    for i in range(0, len(records), BATCH):
        ids = news.upsert_articles(articles[i : i + BATCH])
        rows = [row for r in records[i : i + BATCH] for row in sentiment_rows(r, ids[r["id"]])]
        total += news.replace_sentiment(rows, lens_id=lens.LENS_ID, model_version=lens.MODEL_VERSION)
        print(f"[ingest_news] {min(i + BATCH, len(records))}/{len(records)}", end="\r")
    print(f"\n[ingest_news] 감성 {total}행 적재 (렌즈 {lens.LENS_ID}). worker 를 재시작해 캐시를 비운다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
