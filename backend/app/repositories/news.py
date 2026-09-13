"""news_articles 적재와 시점 경계 조회.

이 테이블은 아직 scripts/check_asof_guard.py 의 감시 대상이 아니다(Q5: T7·T8이
머지된 뒤 현서가 별도 PR로 넓힌다). 그때 파일을 옮기지 않으려고 처음부터
저장소 계층에 뒀다 — 조회 경로가 여기 하나뿐이어야 가드를 넓힐 수 있다.

**시점 경계**: 기사는 발행 시각(published_at)이 시점 태그다. 수집 시각이 아니다.
get_articles_in_window 는 published_at <= as_of 를 반드시 걸고, 그 위에
lookback_hours 하한을 얹는다. 이게 감성 관점의 as_of 규약이다.

**원문 취급 (Q1, 2026-09-12 팀 확정)**: 규칙이 "원문을 저장하지 않는다"에서
"원문을 서비스 응답으로 내보내지 않는다"로 바뀌었다. title/body 를 채우되
보관 범위는 백테스트 구간(2023-01~2025-12)과 운용 시점 수집분으로 한정한다.
**API 응답과 화면에는 제목과 링크만 나간다.** 그래서 조회 함수의 기본값이
include_body=False 다 — 본문이 필요한 곳(감성 분류)만 명시적으로 켜야 한다.
라우터가 아직 스텁이라 강제할 자리가 없어 이 기본값과 README 규칙으로 둔다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text

from app.core.db import get_engine


@dataclass(frozen=True)
class ArticleRow:
    article_id: int
    source: str | None
    title: str | None
    url: str | None
    published_at: datetime
    body: str | None = None


def insert_article(
    *,
    source: str,
    title: str,
    body: str,
    url: str,
    published_at: datetime,
    dedup_hash: str,
    embedding: list[float] | None = None,
) -> int | None:
    """기사 한 건을 적재한다. 이미 있는 기사면 아무것도 하지 않고 None 을 돌려준다.

    중복 판정은 DB 에 걸린 두 UNIQUE 제약이 한다: uq_news_dedup(dedup_hash) 와
    uq_news_articles_url(url). 둘 중 어느 쪽이 걸리든 건너뛰어야 하므로 대상
    없는 ON CONFLICT DO NOTHING 을 쓴다. 이게 중복제거 1단계(정확 일치)다.
    """
    if published_at.tzinfo is None:
        raise ValueError("published_at 은 tz-aware 여야 한다 (소스마다 KST/GMT 가 섞여 온다)")

    with get_engine().begin() as conn:
        return conn.execute(
            text(
                """
                INSERT INTO news_articles
                    (source, title, body, url, published_at, dedup_hash, embedding)
                VALUES
                    (:source, :title, :body, :url, :published_at, :dedup_hash,
                     CAST(:embedding AS vector))
                ON CONFLICT DO NOTHING
                RETURNING article_id
                """
            ),
            {
                "source": source,
                "title": title,
                "body": body,
                "url": url,
                "published_at": published_at,
                "dedup_hash": dedup_hash,
                "embedding": _as_vector_literal(embedding),
            },
        ).scalar_one_or_none()


def get_articles_in_window(
    *, as_of: datetime, lookback_hours: int, include_body: bool = False
) -> list[ArticleRow]:
    """as_of 이하, 최근 lookback_hours 구간의 기사.

    as_of 는 tz-aware datetime 이어야 한다. 거래일(date)을 어느 시각으로 볼지는
    호출자가 정한다 — 장마감 시각인지 자정인지는 판단 계층의 결정이고 여기서
    임의로 정하면 소리 없이 틀린다. T9(daily_judge)에서 팀에 올린다.
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of 는 tz-aware 여야 한다")
    if lookback_hours <= 0:
        raise ValueError(f"lookback_hours 는 1 이상이어야 한다: {lookback_hours!r}")

    since = as_of - timedelta(hours=lookback_hours)
    body_column = "body" if include_body else "NULL AS body"

    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT article_id, source, title, url, published_at, {body_column}
                  FROM news_articles
                 WHERE published_at <= :as_of
                   AND published_at >  :since
                 ORDER BY published_at DESC, article_id DESC
                """  # noqa: S608 — body_column 은 위 리터럴 두 개 중 하나뿐이다
            ),
            {"as_of": as_of, "since": since},
        ).all()

    return [
        ArticleRow(
            article_id=row.article_id,
            source=row.source,
            title=row.title,
            url=row.url,
            published_at=row.published_at,
            body=row.body,
        )
        for row in rows
    ]


def find_similar_articles(
    embedding: list[float], *, as_of: datetime, lookback_hours: int, limit: int = 5
) -> list[tuple[int, float]]:
    """코사인 유사도가 높은 순으로 (article_id, similarity) 를 돌려준다.

    중복제거 2단계다. pgvector 의 <=> 는 코사인 '거리'라 유사도는 1 - 거리다.
    idx_news_embedding(HNSW) 이 이미 걸려 있다.

    1단계(정확 일치)와 섞지 않는다 — 1단계만으로도 수집기가 동작해야 하고,
    이 경로는 임베딩 모델이 있어야만 쓸 수 있다.
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of 는 tz-aware 여야 한다")

    since = as_of - timedelta(hours=lookback_hours)
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT article_id, 1 - (embedding <=> CAST(:embedding AS vector)) AS similarity
                  FROM news_articles
                 WHERE embedding IS NOT NULL
                   AND published_at <= :as_of
                   AND published_at >  :since
                 ORDER BY embedding <=> CAST(:embedding AS vector)
                 LIMIT :limit
                """
            ),
            {
                "embedding": _as_vector_literal(embedding),
                "as_of": as_of,
                "since": since,
                "limit": limit,
            },
        ).all()
    return [(row.article_id, float(row.similarity)) for row in rows]


def _as_vector_literal(embedding: list[float] | None) -> str | None:
    """pgvector 리터럴 문자열. pgvector 파이썬 패키지 없이 넣기 위한 변환이다."""
    if embedding is None:
        return None
    return "[" + ",".join(repr(float(value)) for value in embedding) + "]"
