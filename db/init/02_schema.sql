-- 한국어 투자 전략 명세화 · 백테스트 서비스 스키마 (기반 정리 2단계, 2026-11-02)
--
-- 빈 볼륨에서 postgres 컨테이너가 처음 뜰 때 한 번 돈다(docker-entrypoint-initdb.d).
-- 테이블은 11개다. 정본 설명은 docs/db-erd.md.
--   개인 데이터: users, strategies, strategy_assets, validation_logs, backtest_runs, backtest_metrics
--   공용 데이터: instruments, asset_groups, price_daily, strategy_elements, strategy_element_tags
--
-- 옛 db/migrate/001 (종목 분류 세 축 칸)은 instruments 에 흡수했다.
-- 옛 db/migrate/002 (승인 기록 유일 인덱스), 003 (승인 상태 전이 트리거)은 승인 기능이
-- 없어져 버렸다. 이후 구조 변경은 db/migrate/NNN_설명.sql 로 쌓는다.

-- 계정

CREATE TABLE users (
    user_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email          VARCHAR NOT NULL,
    password_hash  VARCHAR NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_users_email UNIQUE (email)
);

-- 분류표와 종목 원장

CREATE TABLE asset_groups (
    group_id         VARCHAR PRIMARY KEY,
    parent_group_id  VARCHAR REFERENCES asset_groups (group_id),
    group_level      SMALLINT NOT NULL CHECK (group_level IN (1, 2)),
    label            VARCHAR NOT NULL,
    description      TEXT
);

-- 분류는 자산군 / 섹터 / 국가 세 축을 칸마다 따로 둔다. 국가(예: 한국)는 주식과 채권에
-- 모두 걸쳐 있어 asset_groups 트리로 묶을 수 없다. group_id 는 자산군용으로 남아 있다.
CREATE TABLE instruments (
    ticker            VARCHAR PRIMARY KEY,
    name              VARCHAR NOT NULL,
    sector            VARCHAR,
    group_id          VARCHAR NOT NULL REFERENCES asset_groups (group_id),
    risk_tag          VARCHAR,
    mdd_3y            NUMERIC,
    volatility_1y     NUMERIC,
    expense_ratio     NUMERIC,
    listed_date       DATE,
    delisted_date     DATE,
    is_leveraged      BOOLEAN NOT NULL DEFAULT false,
    active            BOOLEAN NOT NULL DEFAULT true,
    asset_group_id    VARCHAR REFERENCES asset_groups (group_id),
    sector_group_id   VARCHAR REFERENCES asset_groups (group_id),
    country_group_id  VARCHAR REFERENCES asset_groups (group_id)
);

-- 전략서

CREATE TABLE strategies (
    strategy_id       VARCHAR PRIMARY KEY,
    user_id           BIGINT NOT NULL REFERENCES users (user_id),
    strategy_version  VARCHAR NOT NULL,
    name              VARCHAR NOT NULL,
    input_prompt      TEXT,
    rebalance         JSONB,
    signal_rules      JSONB,
    constraint_user   JSONB,
    status            VARCHAR NOT NULL CHECK (status IN ('draft', 'approved', 'running', 'closed')),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE strategy_assets (
    strategy_asset_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    strategy_id        VARCHAR NOT NULL REFERENCES strategies (strategy_id),
    ticker             VARCHAR NOT NULL REFERENCES instruments (ticker),
    weight_min         NUMERIC,
    weight_max         NUMERIC,
    weight_min_raw     NUMERIC,
    weight_max_raw     NUMERIC,
    was_adjusted       BOOLEAN NOT NULL DEFAULT false,
    rationale          TEXT
);

-- 검증기는 3단(스키마 · 참조 · 논리)이다.
CREATE TABLE validation_logs (
    validation_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    strategy_id      VARCHAR NOT NULL REFERENCES strategies (strategy_id),
    stage            SMALLINT NOT NULL CHECK (stage BETWEEN 1 AND 3),
    passed           BOOLEAN NOT NULL,
    violations       JSONB,
    adjusted_bounds  JSONB,
    clamped_fields   JSONB,
    checked_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 백테스트

CREATE TABLE backtest_runs (
    run_id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    strategy_id          VARCHAR NOT NULL REFERENCES strategies (strategy_id),
    period_start         DATE,
    period_end           DATE,
    seed_money           NUMERIC,
    fee_rate             NUMERIC,
    tax_rate             NUMERIC,
    slippage_bp          NUMERIC,
    wf_windows           INTEGER,
    random_seed          INTEGER,
    data_snapshot_asof   DATE,
    feature_set_version  VARCHAR,
    status               VARCHAR,
    started_at           TIMESTAMPTZ
);

CREATE TABLE backtest_metrics (
    run_id          BIGINT PRIMARY KEY REFERENCES backtest_runs (run_id),
    cagr            NUMERIC,
    mdd             NUMERIC,
    sharpe          NUMERIC,
    sortino         NUMERIC,
    win_rate        NUMERIC,
    benchmark_cagr  NUMERIC,
    window_results  JSONB
);

-- 가격 (시점 데이터. app/repositories/ 밖에서 직접 읽지 않는다)

CREATE TABLE price_daily (
    ticker       VARCHAR NOT NULL,
    trade_date   DATE NOT NULL,
    open         NUMERIC,
    high         NUMERIC,
    low          NUMERIC,
    close        NUMERIC,
    volume       BIGINT,
    nav          NUMERIC,
    atr_14       NUMERIC,
    ingested_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, trade_date)
);

-- 전략 요소 사전

CREATE TABLE strategy_elements (
    element_id     VARCHAR PRIMARY KEY,
    element_type   VARCHAR NOT NULL CHECK (element_type IN ('indicator', 'filter', 'rebalance', 'etf_trait')),
    title          VARCHAR,
    description    TEXT,
    params_schema  JSONB,
    embedding      VECTOR(768),
    active         BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE strategy_element_tags (
    tag_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    element_id  VARCHAR NOT NULL REFERENCES strategy_elements (element_id),
    tag         VARCHAR NOT NULL
);

-- 인덱스

CREATE INDEX idx_price_ticker_date ON price_daily (ticker, trade_date DESC);
CREATE INDEX idx_strategy_elements_embedding ON strategy_elements USING hnsw (embedding vector_cosine_ops);

-- 하이퍼테이블 전환

SELECT create_hypertable('price_daily', 'trade_date', if_not_exists => TRUE);
