"""news_articles · news_sentiment 적재와 시점 경계 조회 (감성 관점, 2026-10-27 빅카인즈).

두 표는 scripts/check_asof_guard.py 의 감시 대상이다 — 기사 조회는 여기 하나로만 한다.

**시점 경계 = 장 마감.** 판단일 as_of 에는 published_at 이 as_of 날 15:30 KST 이하인 기사만 본다.
15:30 뒤 기사는 다음 날 판단부터다. 세션 집계도 같은 규칙으로 "그날 15:30 이하면 그날, 아니면
다음 날" 의 달력 세션일로 묶는다(주말 · 휴장일을 다음 거래일로 넘기는 것은 판단 계층 몫).

**저장 범위 (2026-10-27 사용자 결정).** 기사별 제목 · 링크 · 언론사 · 입력 시각과 감성 점수까지
저장하고 **본문은 저장하지 않는다**(body = NULL). 본문은 오프라인 채점에만 쓴다.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import text

from app.core.db import get_engine

KST = timezone(timedelta(hours=9))
MARKET_CLOSE_KST = time(15, 30)


def market_close(day: date) -> datetime:
    """그날 장 마감 시각(15:30 KST)."""
    return datetime.combine(day, MARKET_CLOSE_KST, tzinfo=KST)


@dataclass(frozen=True)
class SessionCountRow:
    session: date  # 달력 세션일 (15:30 이하면 그날, 아니면 다음 날)
    sector: str
    n: int
    n_pos: int
    n_neg: int
    mean_polarity: float


def upsert_articles(rows: Sequence[dict]) -> dict[str, int]:
    """기사 메타를 적재한다. dedup_hash(빅카인즈 식별자)가 같으면 덮어쓴다. {dedup_hash: article_id}.

    행: source, title, url(없으면 None), published_at(tz-aware), dedup_hash. 본문은 받지 않는다.
    url 은 UNIQUE 다. **이미 다른 기사(다른 dedup_hash)가 가진 url 이거나 이 배치 안에서 앞 행이
    먼저 쓴 url 이면 None 으로 넣는다** — 연도 파일을 따로 넣거나 다시 넣어도 url 충돌로 배치 전체가
    롤백되지 않게.
    먼저 자리 잡은 기사가 url 을 갖는다(같은 트랜잭션 안에서 확인한다).
    """
    for row in rows:
        if row["published_at"].tzinfo is None:
            raise ValueError("published_at 은 tz-aware 여야 한다")
    if not rows:
        return {}
    with get_engine().begin() as conn:
        urls = sorted({row["url"] for row in rows if row["url"]})
        owners = dict(
            conn.execute(
                text("SELECT url, dedup_hash FROM news_articles WHERE url = ANY(:urls)"), {"urls": urls}
            ).all()
        ) if urls else {}
        claimed: set[str] = set()
        cleaned = []
        for row in rows:
            url = row["url"]
            if url and (owners.get(url, row["dedup_hash"]) != row["dedup_hash"] or url in claimed):
                url = None
            if url:
                claimed.add(url)
            cleaned.append({**row, "url": url})
        rows = cleaned
        conn.execute(
            text(
                """
                INSERT INTO news_articles (source, title, body, url, published_at, dedup_hash)
                VALUES (:source, :title, NULL, :url, :published_at, :dedup_hash)
                ON CONFLICT (dedup_hash) DO UPDATE
                   SET source = EXCLUDED.source,
                       title = EXCLUDED.title,
                       body = NULL,
                       url = EXCLUDED.url,
                       published_at = EXCLUDED.published_at
                """
            ),
            list(rows),
        )
        found = conn.execute(
            text("SELECT dedup_hash, article_id FROM news_articles WHERE dedup_hash = ANY(:hashes)"),
            {"hashes": [row["dedup_hash"] for row in rows]},
        ).all()
    return {row.dedup_hash: row.article_id for row in found}


def replace_sentiment(
    rows: Sequence[tuple[int, str, float, float]], *, lens_id: str, model_version: str
) -> int:
    """(article_id, 묶음, polarity, confidence) 를 넣는다. 같은 기사 · 렌즈의 옛 행은 먼저 지운다(멱등)."""
    if not rows:
        return 0
    ids = sorted({row[0] for row in rows})
    with get_engine().begin() as conn:
        conn.execute(
            text("DELETE FROM news_sentiment WHERE lens_id = :lens_id AND article_id = ANY(:ids)"),
            {"lens_id": lens_id, "ids": ids},
        )
        conn.execute(
            text(
                """
                INSERT INTO news_sentiment (article_id, sector, polarity, confidence, lens_id, model_version)
                VALUES (:article_id, :sector, :polarity, :confidence, :lens_id, :model_version)
                """
            ),
            [
                {"article_id": a, "sector": s, "polarity": p, "confidence": c,
                 "lens_id": lens_id, "model_version": model_version}
                for a, s, p, c in rows
            ],
        )
    return len(rows)


_SESSION_SQL = """
    WITH kst AS (
        SELECT s.sector, s.polarity, a.published_at AT TIME ZONE 'Asia/Seoul' AS local_ts
          FROM news_sentiment s
          JOIN news_articles a ON a.article_id = s.article_id
         WHERE s.lens_id = :lens_id
           AND a.published_at <= :until
           {since_clause}
    )
    SELECT CASE WHEN local_ts::time <= TIME '15:30' THEN local_ts::date
                ELSE local_ts::date + 1 END AS session,
           sector,
           count(*) AS n,
           count(*) FILTER (WHERE polarity >= :edge) AS n_pos,
           count(*) FILTER (WHERE polarity <= -(:edge)) AS n_neg,
           avg(polarity) AS mean_polarity
      FROM kst
     GROUP BY 1, 2
     ORDER BY 1, 2
"""


def get_session_counts(
    *, lens_id: str, as_of: date, polarity_edge: float, since: date | None = None
) -> list[SessionCountRow]:
    """세션 · 묶음별 기사 집계. as_of 날 15:30 KST 이하에 발행된 기사만 센다.

    since 를 주면 since 날 15:30 KST **뒤** 기사만 센다(최근 창만 필요한 판단일 조회용).
    긍정 = polarity >= polarity_edge, 부정 = polarity <= −polarity_edge.
    """
    params: dict = {"lens_id": lens_id, "until": market_close(as_of), "edge": polarity_edge}
    since_clause = ""
    if since is not None:
        since_clause = "AND a.published_at > :since"
        params["since"] = market_close(since)
    with get_engine().connect() as conn:
        rows = conn.execute(text(_SESSION_SQL.format(since_clause=since_clause)), params).all()
    return [
        SessionCountRow(
            session=row.session, sector=row.sector, n=int(row.n), n_pos=int(row.n_pos),
            n_neg=int(row.n_neg), mean_polarity=float(row.mean_polarity),
        )
        for row in rows
    ]


def get_top_articles(
    *, lens_id: str, sector: str, after: date | None, as_of: date, limit: int = 3
) -> list[int]:
    """한 세션의 근거 기사: (after 날 15:30, as_of 날 15:30] KST 에 발행된 그 묶음 기사 중
    |polarity| 큰 순 article_id. after 가 None 이면 하한 없음."""
    params: dict = {"lens_id": lens_id, "sector": sector, "until": market_close(as_of), "limit": limit}
    lower = ""
    if after is not None:
        lower = "AND a.published_at > :after"
        params["after"] = market_close(after)
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT s.article_id
                  FROM news_sentiment s
                  JOIN news_articles a ON a.article_id = s.article_id
                 WHERE s.lens_id = :lens_id
                   AND s.sector = :sector
                   AND a.published_at <= :until
                   {lower}
                 ORDER BY abs(s.polarity) DESC, s.article_id
                 LIMIT :limit
                """  # noqa: S608 — lower 는 위 리터럴 둘 중 하나뿐이다
            ),
            params,
        ).all()
    return [row.article_id for row in rows]


@dataclass(frozen=True)
class ArticleMeta:
    article_id: int
    title: str | None
    source: str | None
    url: str | None
    published_at: datetime


def get_articles(article_ids: Sequence[int]) -> list[ArticleMeta]:
    """근거 기사 표시용 메타(제목 · 언론사 · 링크 · 입력 시각). 본문은 돌려주지 않는다.

    판단근거 화면이 evidence.article_ids 로 부른다. 그 ID 들은 이미 시점 경계를 지나 고른 것이라
    여기서는 as_of 를 받지 않는다 — ID 로만 찾는다. 없는 ID 는 빠진다. 결과는 article_id 순.
    """
    if not article_ids:
        return []
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT article_id, title, source, url, published_at
                  FROM news_articles
                 WHERE article_id = ANY(:ids)
                 ORDER BY article_id
                """
            ),
            {"ids": sorted({int(i) for i in article_ids})},
        ).all()
    return [ArticleMeta(r.article_id, r.title, r.source, r.url, r.published_at) for r in rows]
