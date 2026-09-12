#!/usr/bin/env python3
"""RSS 전방 수집 — 스케줄에 걸어 매일 돌리는 진입점이다.

T10 판정이 "과거 아카이브 확보 불가"로 나왔으므로, 감성 관점을 살리려면 오늘부터
쌓는 수밖에 없다. 하루라도 늦으면 그만큼 줄어든다.

수집·정규화·중복제거 경로는 app/news/collect.py 를 그대로 쓴다(수동 실행용
scripts/ingest_news.py 와 같은 함수다). 이 스크립트가 더하는 것은 **일별 로그**뿐이다.

로그는 JSON Lines 로 쌓는다. 나중에 "어느 날이 비었는가"를 찾을 수 있어야 해서다.
기본 경로는 logs/news_collection.log (.gitignore 의 *.log 에 걸려 커밋되지 않는다).
NEWS_COLLECT_LOG 로 바꿀 수 있다.

멱등하다 — 하루에 여러 번 돌려도 이미 있는 기사는 중복으로 건너뛴다. RSS 가 최근
1~2일치만 주므로 **하루 두 번 이상 돌리는 것이 전제다.** 한 번 놓치면 그날이 빈다.

사용:
    DATABASE_URL=... python scripts/collect_news.py
    DATABASE_URL=... python scripts/collect_news.py --embed
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.news.collect import collect_once  # noqa: E402

DEFAULT_LOG = REPO_ROOT / "logs" / "news_collection.log"
STALE_HOURS = 24


def warn_if_stale(log_path: Path) -> None:
    """직전 24시간 적재가 0건이면 경고한다.

    로그만 쌓아두면 아무도 안 본다. 타이머가 멈췄거나 소스가 통째로 죽은 것을
    **다음 실행이 알려주게** 만드는 게 목적이다. 두 경우를 다 잡는다 —
    24시간 안에 실행 기록이 아예 없거나(타이머 정지), 실행은 됐는데 적재가
    0건이거나(소스 사망·피드 구조 변경).
    """
    if not log_path.exists():
        return

    cutoff = datetime.now(UTC) - timedelta(hours=STALE_HOURS)
    recent = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            collected_at = datetime.fromisoformat(entry["collected_at"])
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
        if collected_at >= cutoff:
            recent.append(entry)

    if not recent:
        print(
            f"[collect_news] 경고: 최근 {STALE_HOURS}시간 안에 실행 기록이 없다. "
            "타이머가 멈췄을 수 있다 (systemctl --user list-timers spec-collect-news.timer)."
        )
        return

    inserted = sum(entry.get("inserted", 0) for entry in recent)
    if inserted == 0:
        failed = sum(entry.get("failed_sources", 0) for entry in recent)
        print(
            f"[collect_news] 경고: 최근 {STALE_HOURS}시간 적재 0건 "
            f"(실행 {len(recent)}회 · 소스 실패 누적 {failed}회). "
            "피드가 죽었거나 구조가 바뀌었을 수 있다."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--embed", action="store_true", help="임베딩까지 계산해 저장한다 (ml extra)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--keep-routine",
        action="store_true",
        help="인사·부고·시세표 같은 정형 기사도 적재한다 (기본은 거른다)",
    )
    args = parser.parse_args()

    result = collect_once(
        embed=args.embed, dry_run=args.dry_run, skip_routine=not args.keep_routine
    )

    log_path = Path(os.environ.get("NEWS_COLLECT_LOG", DEFAULT_LOG))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result.as_dict(), ensure_ascii=False) + "\n")

    for source in result.sources:
        if source.ok:
            print(
                f"[collect_news] {source.source_id}: 항목 {source.entries} · 적재 {source.inserted} "
                f"· 중복 {source.duplicate} · 정형제외 {source.dropped_routine} "
                f"· 시각없음 {source.dropped_no_timestamp}"
            )
        else:
            print(f"[collect_news] {source.source_id}: 실패 {source.error}", file=sys.stderr)

    print(
        f"[collect_news] 적재 {result.inserted} · 중복 {result.duplicate} "
        f"· 소스 실패 {result.failed_sources} · 로그 {log_path}"
    )
    warn_if_stale(log_path)
    # 모든 소스가 실패했을 때만 실패로 본다 — 한둘이 죽어도 수집은 계속돼야 한다.
    return 1 if result.failed_sources == len(result.sources) else 0


if __name__ == "__main__":
    raise SystemExit(main())
