"""news_articles 적재와 시점 경계 조회 테스트. postgres 가 필요하다."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.repositories.news import get_articles_in_window, insert_article

AS_OF = datetime(2026, 9, 10, 15, 30, tzinfo=UTC)


@pytest.fixture
def tag(engine):
    """이 테스트가 넣은 행만 지우기 위한 표식."""
    marker = uuid.uuid4().hex[:10]
    yield marker
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM news_articles WHERE source = :source"), {"source": marker}
        )


def _insert(tag: str, *, body: str, published_at: datetime, url: str | None = None, hash_: str | None = None):
    import hashlib

    normalized = body.strip()
    return insert_article(
        source=tag,
        title=f"제목 {normalized[:10]}",
        body=normalized,
        url=url or f"https://example.com/{tag}/{uuid.uuid4().hex[:8]}",
        published_at=published_at,
        dedup_hash=hash_ or hashlib.sha256(f"{tag}|{normalized}".encode()).hexdigest(),
    )


def test_same_dedup_hash_twice_keeps_one_row(engine, tag) -> None:
    import hashlib

    shared = hashlib.sha256(f"{tag}|같은기사".encode()).hexdigest()
    first = _insert(tag, body="같은 기사 본문", published_at=AS_OF, hash_=shared)
    second = _insert(tag, body="같은 기사 본문", published_at=AS_OF, hash_=shared)

    assert first is not None
    assert second is None  # 건너뛴다

    with engine.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM news_articles WHERE source = :s"), {"s": tag}
        ).scalar_one()
    assert count == 1


def test_same_url_twice_keeps_one_row(engine, tag) -> None:
    url = f"https://example.com/{tag}/same"
    assert _insert(tag, body="본문 하나", published_at=AS_OF, url=url) is not None
    assert _insert(tag, body="본문 둘", published_at=AS_OF, url=url) is None


def test_future_articles_are_not_returned(engine, tag) -> None:
    # 감성 관점의 as_of 규약. 이 경계가 깨지면 백테스트가 무의미해진다.
    _insert(tag, body="현재 기사", published_at=AS_OF - timedelta(minutes=1))
    _insert(tag, body="미래 기사", published_at=AS_OF + timedelta(minutes=1))

    rows = get_articles_in_window(as_of=AS_OF, lookback_hours=24)
    titles = [row.title for row in rows if row.source == tag]

    assert len(titles) == 1
    assert "현재" in titles[0]


def test_articles_outside_the_lookback_are_not_returned(engine, tag) -> None:
    _insert(tag, body="윈도우 안", published_at=AS_OF - timedelta(hours=5))
    _insert(tag, body="윈도우 밖", published_at=AS_OF - timedelta(hours=30))

    rows = [r for r in get_articles_in_window(as_of=AS_OF, lookback_hours=24) if r.source == tag]
    assert len(rows) == 1
    assert "안" in rows[0].title


def test_body_is_withheld_unless_asked(engine, tag) -> None:
    # Q1 규칙: 원문은 보관하되 서비스 응답에는 제목과 링크만 나간다.
    _insert(tag, body="본문이 여기 있다", published_at=AS_OF - timedelta(hours=1))

    default_rows = [r for r in get_articles_in_window(as_of=AS_OF, lookback_hours=24) if r.source == tag]
    with_body = [
        r
        for r in get_articles_in_window(as_of=AS_OF, lookback_hours=24, include_body=True)
        if r.source == tag
    ]

    assert default_rows[0].body is None
    assert default_rows[0].title is not None
    assert with_body[0].body == "본문이 여기 있다"


def test_naive_datetimes_are_rejected(engine, tag) -> None:
    naive = datetime(2026, 9, 10, 15, 30)
    with pytest.raises(ValueError, match="tz-aware"):
        get_articles_in_window(as_of=naive, lookback_hours=24)
    with pytest.raises(ValueError, match="tz-aware"):
        insert_article(
            source=tag,
            title="t",
            body="b",
            url="https://example.com/naive",
            published_at=naive,
            dedup_hash="x" * 64,
        )


def test_lookback_hours_must_be_positive(engine) -> None:
    with pytest.raises(ValueError, match="1 이상"):
        get_articles_in_window(as_of=AS_OF, lookback_hours=0)
