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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.news.collect import collect_once  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="적재하지 않고 집계만 출력한다")
    parser.add_argument(
        "--embed", action="store_true", help="임베딩까지 계산해 저장한다 (ml extra 필요)"
    )
    parser.add_argument(
        "--keep-routine",
        action="store_true",
        help="인사·부고·시세표 같은 정형 기사도 적재한다 (기본은 거른다)",
    )
    args = parser.parse_args()

    result = collect_once(
        embed=args.embed, dry_run=args.dry_run, skip_routine=not args.keep_routine
    )
    for source in result.sources:
        if source.ok:
            print(
                f"[ingest_news] {source.source_id}: 항목 {source.entries} · 유효 {source.usable} "
                f"· 적재 {source.inserted} · 중복 {source.duplicate} "
                f"· 정형제외 {source.dropped_routine} · 시각없음 {source.dropped_no_timestamp}"
            )
        else:
            print(f"[ingest_news] {source.source_id} 수집 실패: {source.error}", file=sys.stderr)

    print(f"[ingest_news] 적재 {result.inserted}건 · 중복으로 건너뜀 {result.duplicate}건 "
          f"· 소스 실패 {result.failed_sources}개")
    return 1 if result.failed_sources == len(result.sources) else 0


if __name__ == "__main__":
    raise SystemExit(main())
