"""근거 기사 조회 API (GET /news/articles) — postgres 가 필요하다."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.repositories import news

KST = timezone(timedelta(hours=9))


@pytest.fixture
def article(engine):
    key = f"api-test.{uuid.uuid4().hex}"
    ids = news.upsert_articles([{"source": "한국경제", "title": "국채금리 급등", "url": None,
                                 "published_at": datetime(2023, 3, 2, 10, 0, tzinfo=KST), "dedup_hash": key}])
    yield ids[key]
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM news_articles WHERE dedup_hash = :k"), {"k": key})


def _auth(client, make_user):
    email, _ = make_user("retail")
    tokens = client.post("/auth/login", json={"username": email}).json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def test_requires_login(client):
    assert client.get("/news/articles", params={"ids": "1"}).status_code == 401


def test_returns_title_without_body(client, make_user, article):
    resp = client.get("/news/articles", params={"ids": f"{article},999999999"},
                      headers=_auth(client, make_user))
    assert resp.status_code == 200
    [item] = resp.json()
    assert item["article_id"] == article and item["title"] == "국채금리 급등"
    assert set(item) == {"article_id", "title", "source", "url", "published_at"}


@pytest.mark.parametrize("ids", ["", "a,b", ",".join(str(i) for i in range(51))])
def test_rejects_bad_ids(client, make_user, ids):
    resp = client.get("/news/articles", params={"ids": ids}, headers=_auth(client, make_user))
    assert resp.status_code == 422
