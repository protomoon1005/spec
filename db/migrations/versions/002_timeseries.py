"""timeseries

Revision ID: 002_timeseries
Revises: 001_core
Create Date: 2026-09-08 23:44:44.795023

데이터·지식 도메인 9개 테이블. 컬럼 정의는 docs/db-erd.md 4.2를 그대로 따른다.
하이퍼테이블 전환(create_hypertable)은 003_hypertables로 분리한다 — 여기서는
PK/인덱스를 포함한 "평범한" 테이블까지만 만든다 (충돌 시 후반부로 분리하라는
infra-spec.md 3장 지시).

NEWS_ARTICLES.title/body: db-erd.md 4.2에는 있지만, graduation-project 최상위
CLAUDE.md의 "뉴스 원문은 저장하지 않는다. 메타정보·감성 점수·태그·임베딩만 저장한다"
규칙과 정면으로 충돌한다. 컬럼 자체는 정본(db-erd.md)을 따라 만들되, 실제로 원문을
채워 넣을지는 이 마이그레이션의 범위 밖(수집기 구현 시점)에서 결정해야 한다.
README "알려진 설계 구멍"에도 기록해 뒀다.

임베딩 컬럼(NEWS_ARTICLES.embedding, BBL_BLOCKS.embedding)은 차원이 다를 수 있어
(BBL 검색 M1 / 뉴스 중복제거 M3, 담당·모델이 다름) 정의와 인덱스를 테이블별로
따로 둔다. 지금은 둘 다 768(사용자 확정)이지만 나중에 한쪽만 바꿀 수 있다.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '002_timeseries'
down_revision: Union[str, Sequence[str], None] = '001_core'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLES: list[tuple[str, str]] = [
    ("price_daily", """
        CREATE TABLE price_daily (
            ticker      VARCHAR NOT NULL,
            trade_date  DATE NOT NULL,
            open        NUMERIC,
            high        NUMERIC,
            low         NUMERIC,
            close       NUMERIC,
            volume      BIGINT,
            nav         NUMERIC,
            atr_14      NUMERIC,
            ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (ticker, trade_date)
        )
    """),
    ("feature_store", """
        CREATE TABLE feature_store (
            ticker              VARCHAR NOT NULL,
            as_of               DATE NOT NULL,
            feature_set_version VARCHAR NOT NULL,
            features            JSONB,
            computed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (ticker, as_of, feature_set_version)
        )
    """),
    ("macro_indicators", """
        CREATE TABLE macro_indicators (
            indicator_code VARCHAR NOT NULL,
            as_of          DATE NOT NULL,
            value          NUMERIC,
            released_at    DATE NOT NULL,
            source         VARCHAR CHECK (source IN ('ecos', 'fred', 'krx')),
            PRIMARY KEY (indicator_code, as_of)
        )
    """),
    ("regime_snapshots", """
        CREATE TABLE regime_snapshots (
            as_of           DATE NOT NULL,
            trend_index     VARCHAR NOT NULL,
            regime_label    VARCHAR,
            intensity       NUMERIC,
            threshold_state JSONB,
            PRIMARY KEY (as_of, trend_index)
        )
    """),
    # embedding 차원(768)은 db-erd.md에 없다 — 사용자 확정값. 임베딩 모델이
    # 바뀌면 이 컬럼 타입(vector(768))과 idx_news_embedding을 함께 재검토해야 한다.
    # -- TODO: 임베딩 모델 확정 후 차원 재검토 (뉴스 중복제거, M3 담당)
    ("news_articles", """
        CREATE TABLE news_articles (
            article_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            source       VARCHAR,
            title        TEXT,
            body         TEXT,
            url          VARCHAR,
            published_at TIMESTAMPTZ,
            dedup_hash   VARCHAR,
            embedding    VECTOR(768),
            CONSTRAINT uq_news_articles_url UNIQUE (url)
        )
    """),
    ("news_sentiment", """
        CREATE TABLE news_sentiment (
            sentiment_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            article_id    BIGINT NOT NULL REFERENCES news_articles (article_id),
            sector        VARCHAR,
            polarity      NUMERIC,
            confidence    NUMERIC,
            lens_id       VARCHAR,
            model_version VARCHAR
        )
    """),
    # -- TODO: 임베딩 모델 확정 후 차원 재검토 (BBL 검색, M1 담당)
    ("bbl_blocks", """
        CREATE TABLE bbl_blocks (
            block_id      VARCHAR PRIMARY KEY,
            block_type    VARCHAR NOT NULL CHECK (block_type IN ('indicator', 'filter', 'rebalance', 'lens', 'etf_trait')),
            title         VARCHAR,
            description   TEXT,
            params_schema JSONB,
            embedding     VECTOR(768),
            active        BOOLEAN NOT NULL DEFAULT true
        )
    """),
    ("bbl_tags", """
        CREATE TABLE bbl_tags (
            tag_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            block_id VARCHAR NOT NULL REFERENCES bbl_blocks (block_id),
            tag      VARCHAR NOT NULL
        )
    """),
    ("ml_models", """
        CREATE TABLE ml_models (
            model_version      VARCHAR PRIMARY KEY,
            view_type          VARCHAR CHECK (view_type IN ('market', 'sentiment', 'regime')),
            train_start        DATE,
            train_end          DATE,
            accuracy           NUMERIC,
            brier_score        NUMERIC,
            calibration_method VARCHAR,
            artifact_uri       VARCHAR,
            is_active          BOOLEAN NOT NULL DEFAULT false,
            trained_at         TIMESTAMPTZ
        )
    """),
]

# db-erd.md 4.4 인덱스 목록 중 이 파일 소속 테이블의 인덱스.
# (하이퍼테이블 전환 자체는 003_hypertables.)
_INDEXES: list[str] = [
    "CREATE INDEX idx_feature_ticker_asof ON feature_store (ticker, as_of DESC)",
    "CREATE INDEX idx_price_ticker_date ON price_daily (ticker, trade_date DESC)",
    "CREATE INDEX idx_macro_code_asof ON macro_indicators (indicator_code, as_of DESC, released_at DESC)",
    "CREATE INDEX idx_news_published ON news_articles (published_at DESC)",
    "CREATE UNIQUE INDEX uq_news_dedup ON news_articles (dedup_hash)",
    "CREATE INDEX idx_bbl_embedding ON bbl_blocks USING hnsw (embedding vector_cosine_ops)",
    "CREATE INDEX idx_news_embedding ON news_articles USING hnsw (embedding vector_cosine_ops)",
]


def upgrade() -> None:
    for _name, ddl in _TABLES:
        op.execute(ddl)
    for stmt in _INDEXES:
        op.execute(stmt)


def downgrade() -> None:
    for name, _ddl in reversed(_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {name} CASCADE")
