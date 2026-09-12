# RSS/Atom 파싱. stdlib(xml.etree) 만 쓴다.
#
# feedparser 는 ml extra 라 여기서 쓰면 수집 경로 전체가 CI 밖으로 나간다.
# RSS 2.0 의 <item> 과 Atom 의 <entry> 두 형태만 다루면 되므로 직접 읽는다.
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime

from app.news.normalize import dedup_hash, normalize_body, parse_published_at

_ATOM = "{http://www.w3.org/2005/Atom}"
_CONTENT = "{http://purl.org/rss/1.0/modules/content/}encoded"
_DC_DATE = "{http://purl.org/dc/elements/1.1/}date"


@dataclass(frozen=True)
class FeedItem:
    source_id: str
    title: str
    body: str
    url: str
    published_at: datetime  # tz-aware UTC
    dedup_hash: str


@dataclass(frozen=True)
class FeedParseResult:
    items: tuple[FeedItem, ...]
    total: int
    dropped_no_timestamp: int
    dropped_no_url: int
    dropped_empty_text: int

    @property
    def oldest(self) -> datetime | None:
        return min((item.published_at for item in self.items), default=None)

    @property
    def newest(self) -> datetime | None:
        return max((item.published_at for item in self.items), default=None)


def parse_feed(xml_bytes: bytes, *, source_id: str) -> FeedParseResult:
    """피드 XML 을 FeedItem 목록으로. 시각을 못 읽은 기사는 버리고 센다."""
    root = ET.fromstring(xml_bytes)
    entries = root.findall(".//item") or root.findall(f".//{_ATOM}entry")

    items: list[FeedItem] = []
    no_timestamp = no_url = empty_text = 0

    for entry in entries:
        date_tags = ("pubDate", _DC_DATE, f"{_ATOM}published", f"{_ATOM}updated")
        published_at = parse_published_at(_first_text(entry, date_tags))
        if published_at is None:
            no_timestamp += 1
            continue

        url = _first_text(entry, ("link", "guid")) or _atom_link(entry)
        if not url:
            no_url += 1
            continue

        title = _first_text(entry, ("title", f"{_ATOM}title")) or ""
        body_tags = (_CONTENT, "description", f"{_ATOM}summary", f"{_ATOM}content")
        raw_body = _first_text(entry, body_tags) or ""
        body = normalize_body(raw_body)
        if not body and not title.strip():
            empty_text += 1
            continue

        items.append(
            FeedItem(
                source_id=source_id,
                title=title.strip(),
                body=body,
                url=url.strip(),
                published_at=published_at,
                dedup_hash=dedup_hash(body, title=title),
            )
        )

    return FeedParseResult(
        items=tuple(items),
        total=len(entries),
        dropped_no_timestamp=no_timestamp,
        dropped_no_url=no_url,
        dropped_empty_text=empty_text,
    )


def _first_text(entry: ET.Element, tags: tuple[str, ...]) -> str | None:
    for tag in tags:
        found = entry.find(tag)
        if found is not None and found.text and found.text.strip():
            return found.text
    return None


def _atom_link(entry: ET.Element) -> str | None:
    for link in entry.findall(f"{_ATOM}link"):
        href = link.get("href")
        if href and link.get("rel", "alternate") == "alternate":
            return href
    return None
