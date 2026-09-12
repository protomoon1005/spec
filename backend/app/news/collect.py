# RSS 수집 한 바퀴. scripts/ 의 두 진입점(수동 ingest_news, 스케줄 collect_news)이
# 이 함수를 함께 쓴다 — 수집 경로가 둘로 갈라지면 어느 쪽이 무엇을 걸렀는지
# 대조할 수 없다.
#
# 설명을 docstring 이 아니라 주석에 두는 이유는 다른 app/ 모듈과 같다.
from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from app.news.normalize import is_routine_notice
from app.news.rss import parse_feed
from app.news.sources import RSS_SOURCES

USER_AGENT = "Mozilla/5.0 (spec-graduation-project; RSS reader)"
TIMEOUT_SECONDS = 20


@dataclass
class SourceResult:
    source_id: str
    ok: bool
    entries: int = 0
    usable: int = 0
    dropped_no_timestamp: int = 0
    dropped_no_url: int = 0
    dropped_empty_text: int = 0
    dropped_routine: int = 0
    inserted: int = 0
    duplicate: int = 0
    error: str = ""


@dataclass
class CollectionResult:
    collected_at: str
    sources: list[SourceResult] = field(default_factory=list)

    @property
    def inserted(self) -> int:
        return sum(source.inserted for source in self.sources)

    @property
    def duplicate(self) -> int:
        return sum(source.duplicate for source in self.sources)

    @property
    def failed_sources(self) -> int:
        return sum(1 for source in self.sources if not source.ok)

    def as_dict(self) -> dict:
        return {
            "collected_at": self.collected_at,
            "inserted": self.inserted,
            "duplicate": self.duplicate,
            "failed_sources": self.failed_sources,
            "sources": [asdict(source) for source in self.sources],
        }


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
        return response.read()


def collect_once(
    *, embed: bool = False, dry_run: bool = False, skip_routine: bool = True
) -> CollectionResult:
    """모든 소스를 한 바퀴 돌며 적재한다. 여러 번 돌려도 안전하다.

    멱등성은 DB 의 두 UNIQUE 제약(dedup_hash · url)이 보장한다 — 이미 있는 기사는
    건너뛰고 duplicate 로 센다. 같은 날 여러 번 돌리는 것이 전제다(RSS 가 최근
    1~2일치만 주므로 한 번 놓치면 그날이 빈다).
    """
    embed_texts = None
    if embed:
        from app.news.embedding import embed_texts  # noqa: PLC0415

    result = CollectionResult(collected_at=datetime.now(UTC).isoformat())

    for source in RSS_SOURCES:
        try:
            raw = fetch(source.url)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            result.sources.append(
                SourceResult(source_id=source.source_id, ok=False, error=f"{type(exc).__name__}: {exc}")
            )
            continue

        parsed = parse_feed(raw, source_id=source.source_id)
        items = list(parsed.items)
        routine = 0
        if skip_routine:
            kept = [item for item in items if not is_routine_notice(item.title)]
            routine = len(items) - len(kept)
            items = kept

        stat = SourceResult(
            source_id=source.source_id,
            ok=True,
            entries=parsed.total,
            usable=len(items),
            dropped_no_timestamp=parsed.dropped_no_timestamp,
            dropped_no_url=parsed.dropped_no_url,
            dropped_empty_text=parsed.dropped_empty_text,
            dropped_routine=routine,
        )

        if not dry_run and items:
            vectors = None
            if embed_texts is not None:
                vectors = embed_texts([f"{item.title} {item.body}"[:600] for item in items])

            from app.repositories.news import insert_article  # noqa: PLC0415

            for index, item in enumerate(items):
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
                    stat.duplicate += 1
                else:
                    stat.inserted += 1

        result.sources.append(stat)

    return result
