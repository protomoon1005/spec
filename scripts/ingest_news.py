#!/usr/bin/env python3
"""RSS 소스에서 기사를 받아 news_articles 에 적재한다 (FN-404).

중복제거는 두 단계다.
  1단계 정확 일치 — 정규화 본문의 해시. DB의 uq_news_dedup / uq_news_articles_url
        UNIQUE 제약이 막는다. 의존성이 stdlib 뿐이라 이 스크립트의 기본 동작이다.
  2단계 임베딩 유사도 — 재게재·부분 수정 기사를 잡는다. transformers 가 필요해
        --embed 를 줄 때만 돈다 (ml extra).

Celery 태스크로 묶지 않았다. 태스크 이름 6종(compile_spec · run_backtest ·
daily_judge · ingest_market · train_model · update_view_weights)에 뉴스 수집이
없어서, ingest_market 에 얹을지 이름을 늘릴지는 계약 문제다. T9(daily_judge
결선)에서 팀에 올린다.

사용:
    DATABASE_URL=... python scripts/ingest_news.py
    DATABASE_URL=... python scripts/ingest_news.py --embed --dry-run
"""
from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.news.rss import parse_feed  # noqa: E402
from app.news.sources import RSS_SOURCES  # noqa: E402

USER_AGENT = "Mozilla/5.0 (spec-graduation-project; RSS reader)"
TIMEOUT_SECONDS = 20


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
        return response.read()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="적재하지 않고 집계만 출력한다")
    parser.add_argument(
        "--embed", action="store_true", help="임베딩까지 계산해 저장한다 (ml extra 필요)"
    )
    args = parser.parse_args()

    embed_texts = None
    if args.embed:
        from app.news.embedding import embed_texts  # noqa: PLC0415

    inserted = skipped = failed_sources = 0
    for source in RSS_SOURCES:
        try:
            raw = fetch(source.url)
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"[ingest_news] {source.source_id} 수집 실패: {exc}", file=sys.stderr)
            failed_sources += 1
            continue

        result = parse_feed(raw, source_id=source.source_id)
        print(
            f"[ingest_news] {source.source_id}: 항목 {result.total} · 유효 {len(result.items)} "
            f"· 시각없음 {result.dropped_no_timestamp} · 링크없음 {result.dropped_no_url} "
            f"· 본문없음 {result.dropped_empty_text}"
        )
        if args.dry_run:
            continue

        vectors = None
        if embed_texts is not None and result.items:
            vectors = embed_texts([f"{item.title} {item.body}"[:600] for item in result.items])

        from app.repositories.news import insert_article  # noqa: PLC0415

        for index, item in enumerate(result.items):
            article_id = insert_article(
                source=item.source_id,
                title=item.title,
                body=item.body,
                url=item.url,
                published_at=item.published_at,
                dedup_hash=item.dedup_hash,
                embedding=None if vectors is None else vectors[index],
            )
            if article_id is None:
                skipped += 1
            else:
                inserted += 1

    print(f"[ingest_news] 적재 {inserted}건 · 중복으로 건너뜀 {skipped}건 · 소스 실패 {failed_sources}개")
    return 1 if failed_sources == len(RSS_SOURCES) else 0


if __name__ == "__main__":
    raise SystemExit(main())
