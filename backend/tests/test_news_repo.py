"""app/repositories/news.py — 장 마감(15:30 KST) 경계와 세션 집계. postgres 가 필요하다."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.repositories import news

KST = timezone(timedelta(hours=9))
DAY = date(2020, 3, 2)  # 월


@pytest.fixture
def lens_id(engine):
    lens = f"test-{uuid.uuid4().hex[:8]}"
    created: list[str] = []
    yield lens, created
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM news_sentiment WHERE lens_id = :l"), {"l": lens})
        conn.execute(text("DELETE FROM news_articles WHERE dedup_hash = ANY(:h)"), {"h": created})


def _put(lens, created, when: datetime, polarity: float, sector: str = "EQUITY") -> int:
    key = f"{lens}.{uuid.uuid4().hex}"
    created.append(key)
    ids = news.upsert_articles([{"source": "s", "title": "t", "url": None, "published_at": when,
                                 "dedup_hash": key}])
    news.replace_sentiment([(ids[key], sector, polarity, abs(polarity))], lens_id=lens, model_version="m")
    return ids[key]


def test_market_close_boundary_and_session_rollover(lens_id):
    lens, created = lens_id
    _put(lens, created, datetime(2020, 3, 2, 15, 30, tzinfo=KST), 0.9)   # 마감 정각 -> 그날
    _put(lens, created, datetime(2020, 3, 2, 15, 31, tzinfo=KST), -0.9)  # 마감 뒤 -> 다음 날
    _put(lens, created, datetime(2020, 3, 2, 9, 0, tzinfo=KST), 0.1)    # 중립

    same_day = news.get_session_counts(lens_id=lens, as_of=DAY, polarity_edge=0.5)
    assert [(r.session, r.n, r.n_pos, r.n_neg) for r in same_day] == [(DAY, 2, 1, 0)]

    next_day = news.get_session_counts(lens_id=lens, as_of=DAY + timedelta(days=1), polarity_edge=0.5)
    assert [(r.session, r.n, r.n_pos, r.n_neg) for r in next_day] == [
        (DAY, 2, 1, 0), (DAY + timedelta(days=1), 1, 0, 1)
    ]
    windowed = news.get_session_counts(lens_id=lens, as_of=DAY + timedelta(days=1), polarity_edge=0.5,
                                       since=DAY)
    assert [(r.session, r.n) for r in windowed] == [(DAY + timedelta(days=1), 1)]


def test_top_articles_are_bounded_by_the_session(lens_id):
    lens, created = lens_id
    inside = _put(lens, created, datetime(2020, 3, 2, 10, 0, tzinfo=KST), -0.95)
    mild = _put(lens, created, datetime(2020, 3, 2, 11, 0, tzinfo=KST), 0.2)
    _put(lens, created, datetime(2020, 2, 28, 10, 0, tzinfo=KST), 0.99)  # 직전 세션
    _put(lens, created, datetime(2020, 3, 2, 16, 0, tzinfo=KST), 0.99)   # 마감 뒤
    got = news.get_top_articles(lens_id=lens, sector="EQUITY", after=date(2020, 2, 28), as_of=DAY)
    assert got == [inside, mild]


def test_upsert_is_idempotent_and_never_stores_body(lens_id, engine):
    lens, created = lens_id
    when = datetime(2020, 3, 2, 10, 0, tzinfo=KST)
    first = _put(lens, created, when, 0.6)
    key = created[-1]
    again = news.upsert_articles([{"source": "s2", "title": "t2", "url": None, "published_at": when,
                                   "dedup_hash": key}])
    news.replace_sentiment([(first, "EQUITY", 0.7, 0.7)], lens_id=lens, model_version="m")
    assert again[key] == first
    with engine.connect() as conn:
        body = conn.execute(
            text("SELECT body FROM news_articles WHERE article_id = :a"), {"a": first}
        ).scalar()
        n = conn.execute(text("SELECT count(*) FROM news_sentiment WHERE lens_id = :l"), {"l": lens}).scalar()
    assert body is None and n == 1
    with pytest.raises(ValueError):
        news.upsert_articles([{"source": "s", "title": "t", "url": None,
                               "published_at": datetime(2020, 3, 2), "dedup_hash": "x"}])


def test_url_owned_by_another_article_is_stored_as_null(lens_id, engine):
    # 따로 넣은 파일 · 재적재에서 DB 에 이미 있는 url 과 겹쳐도 롤백되지 않는다.
    lens, created = lens_id
    url = f"https://example.com/{uuid.uuid4().hex}"
    first, second, third = (f"{lens}.{uuid.uuid4().hex}" for _ in range(3))
    created.extend([first, second, third])
    when = datetime(2020, 3, 2, 10, 0, tzinfo=KST)

    def row(key):
        return {"source": "s", "title": "t", "url": url, "published_at": when, "dedup_hash": key}

    news.upsert_articles([row(first)])
    ids = news.upsert_articles([row(second), row(third), row(first)])  # 기존 주인과 배치 안 중복
    with engine.connect() as conn:
        got = dict(conn.execute(
            text("SELECT dedup_hash, url FROM news_articles WHERE article_id = ANY(:ids)"),
            {"ids": list(ids.values())},
        ).all())
    assert got == {first: url, second: None, third: None}
