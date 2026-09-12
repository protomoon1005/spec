"""뉴스 정규화·해시·시각 파싱 테스트.

픽스처도 ML 의존성도 요구하지 않는다 — 중복제거 1단계(정확 일치)와 타임존
처리는 수집기의 뼈대라 CI 에서 상시 돌아야 한다.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.news.normalize import dedup_hash, normalize_body, parse_published_at
from app.news.rss import parse_feed

BODY = "[서울=연합뉴스] 홍길동 기자 = 코스피가 상승 마감했다. reporter@example.com"


def test_boilerplate_is_removed() -> None:
    normalized = normalize_body(BODY + " 무단전재 및 재배포 금지")

    assert "기자" not in normalized
    assert "@" not in normalized
    assert "무단전재" not in normalized
    assert "코스피가 상승 마감했다." in normalized


def test_same_article_gives_same_hash() -> None:
    assert dedup_hash(normalize_body(BODY)) == dedup_hash(normalize_body(BODY))


def test_whitespace_only_differences_hash_the_same() -> None:
    spaced = "코스피가   상승\n\n마감했다.\t"
    tight = "코스피가 상승 마감했다."
    assert dedup_hash(normalize_body(spaced)) == dedup_hash(normalize_body(tight))


def test_copyright_trailer_does_not_change_the_hash() -> None:
    plain = "코스피가 상승 마감했다."
    with_trailer = plain + " 저작권자 ⓒ 어떤신문 무단전재 및 재배포 금지"
    assert dedup_hash(normalize_body(with_trailer)) == dedup_hash(normalize_body(plain))


def test_different_articles_hash_differently() -> None:
    assert dedup_hash(normalize_body("코스피가 상승 마감했다.")) != dedup_hash(
        normalize_body("코스닥이 하락 마감했다.")
    )


def test_html_entities_and_tags_are_stripped() -> None:
    normalized = normalize_body("&lt;p&gt;코스피가 &amp;lsquo;상승&amp;rsquo; 마감&lt;/p&gt;")
    assert "<" not in normalized and "&" not in normalized
    assert "코스피가" in normalized


def test_empty_body_falls_back_to_title() -> None:
    assert dedup_hash("", title="코스피 상승 마감")
    with pytest.raises(ValueError, match="비어 있어"):
        dedup_hash("", title="")


# ── 타임존 ───────────────────────────────────────────────────────────


def test_kst_and_gmt_notations_are_the_same_instant() -> None:
    kst = parse_published_at("Fri, 11 Sep 2026 10:11:51 +0900")
    gmt = parse_published_at("Fri, 11 Sep 2026 01:11:51 GMT")
    assert kst == gmt == datetime(2026, 9, 11, 1, 11, 51, tzinfo=UTC)


def test_iso8601_is_accepted() -> None:
    assert parse_published_at("2026-09-11T01:11:51Z") == datetime(
        2026, 9, 11, 1, 11, 51, tzinfo=UTC
    )


def test_offset_with_colon_is_accepted() -> None:
    # 실측(2026-09-12): 매일경제 RSS 가 "+09:00" 으로 준다. RFC 822 는 "+0900" 이라
    # 표준 파서가 예외 없이 naive 를 돌려준다 — 그러면 조용히 9시간을 잃는다.
    assert parse_published_at("Sat, 12 Sep 2026 18:10:40 +09:00") == datetime(
        2026, 9, 12, 9, 10, 40, tzinfo=UTC
    )


@pytest.mark.parametrize("raw", [None, "", "   ", "어제", "2026-09-11T01:11:51"])
def test_unusable_timestamps_are_rejected(raw: str | None) -> None:
    # 마지막 케이스는 tz 가 없는 naive 다. 시각을 모르는 기사는 쓸 수 없다.
    assert parse_published_at(raw) is None


# ── 피드 파싱 ────────────────────────────────────────────────────────

_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>코스피 상승 마감</title>
    <link>https://example.com/1</link>
    <description>코스피가 상승 마감했다. 홍길동 기자</description>
    <pubDate>Fri, 11 Sep 2026 10:11:51 +0900</pubDate>
  </item>
  <item>
    <title>시각이 없는 기사</title>
    <link>https://example.com/2</link>
    <description>본문은 있다.</description>
  </item>
  <item>
    <title>naive 시각 기사</title>
    <link>https://example.com/3</link>
    <description>본문은 있다.</description>
    <pubDate>2026-09-11T01:11:51</pubDate>
  </item>
</channel></rss>
"""


def test_items_without_usable_timestamp_are_dropped_and_counted() -> None:
    result = parse_feed(_FEED.encode("utf-8"), source_id="test")

    assert result.total == 3
    assert len(result.items) == 1
    assert result.dropped_no_timestamp == 2  # 시각 없음 + naive
    assert result.items[0].published_at == datetime(2026, 9, 11, 1, 11, 51, tzinfo=UTC)
    assert result.items[0].source_id == "test"
    assert "기자" not in result.items[0].body
