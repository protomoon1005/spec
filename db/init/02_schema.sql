-- 전략·운용 도메인 테이블 (기존 001_core.py 에서 추출)

CREATE TABLE users (
    user_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email          VARCHAR NOT NULL,
    password_hash  VARCHAR NOT NULL,
    role           VARCHAR NOT NULL CHECK (role IN ('retail', 'pro', 'admin')),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_users_email UNIQUE (email)
);

CREATE TABLE preset_versions (
    preset_version VARCHAR PRIMARY KEY,
    description    TEXT,
    created_by     BIGINT NOT NULL REFERENCES users (user_id),
    activated_at   TIMESTAMPTZ,
    is_active      BOOLEAN NOT NULL DEFAULT false
);

CREATE TABLE hardcap_versions (
    hardcap_version       VARCHAR PRIMARY KEY,
    max_weight_per_asset  NUMERIC NOT NULL,
    cash_min              NUMERIC NOT NULL,
    max_loss_per_trade    NUMERIC NOT NULL,
    max_drawdown          NUMERIC NOT NULL,
    min_interval_days     INTEGER NOT NULL,
    leverage_allowed      BOOLEAN NOT NULL,
    created_by            BIGINT NOT NULL REFERENCES users (user_id),
    activated_at          TIMESTAMPTZ
);

CREATE TABLE risk_profile_defaults (
    preset_version               VARCHAR NOT NULL REFERENCES preset_versions (preset_version),
    risk_level                   SMALLINT NOT NULL CHECK (risk_level BETWEEN 1 AND 5),
    cash_min_default             NUMERIC NOT NULL,
    max_drawdown_default         NUMERIC NOT NULL,
    max_loss_per_trade_default   NUMERIC NOT NULL,
    PRIMARY KEY (preset_version, risk_level)
);

CREATE TABLE risk_profiles (
    profile_id                   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id                      BIGINT NOT NULL REFERENCES users (user_id),
    risk_level                   SMALLINT NOT NULL CHECK (risk_level BETWEEN 1 AND 5),
    preset_version               VARCHAR NOT NULL REFERENCES preset_versions (preset_version),
    cash_min_default             NUMERIC,
    max_drawdown_default         NUMERIC,
    max_loss_per_trade_default   NUMERIC,
    provenance                   JSONB,
    created_at                   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE survey_responses (
    response_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id        BIGINT NOT NULL REFERENCES users (user_id),
    question_code  VARCHAR NOT NULL,
    answer_code    VARCHAR NOT NULL,
    score          SMALLINT,
    answered_at    TIMESTAMPTZ
);

CREATE TABLE asset_bound_presets (
    preset_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    preset_version VARCHAR NOT NULL REFERENCES preset_versions (preset_version),
    risk_level     SMALLINT NOT NULL CHECK (risk_level BETWEEN 1 AND 5),
    risk_tag       VARCHAR NOT NULL,
    allowed_min    NUMERIC NOT NULL,
    allowed_max    NUMERIC NOT NULL,
    rationale      TEXT
);

CREATE TABLE asset_groups (
    group_id         VARCHAR PRIMARY KEY,
    parent_group_id  VARCHAR REFERENCES asset_groups (group_id),
    group_level      SMALLINT NOT NULL CHECK (group_level IN (1, 2)),
    label            VARCHAR NOT NULL,
    description      TEXT
);

CREATE TABLE group_caps (
    group_cap_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    group_id         VARCHAR NOT NULL REFERENCES asset_groups (group_id),
    preset_version   VARCHAR NOT NULL REFERENCES preset_versions (preset_version),
    risk_level       SMALLINT NOT NULL CHECK (risk_level BETWEEN 0 AND 5),
    max_total_weight NUMERIC NOT NULL
);

CREATE TABLE etf_master (
    ticker          VARCHAR PRIMARY KEY,
    name            VARCHAR NOT NULL,
    sector          VARCHAR,
    group_id        VARCHAR NOT NULL REFERENCES asset_groups (group_id),
    risk_tag        VARCHAR,
    mdd_3y          NUMERIC,
    volatility_1y   NUMERIC,
    expense_ratio   NUMERIC,
    listed_date     DATE,
    delisted_date   DATE,
    is_leveraged    BOOLEAN NOT NULL DEFAULT false,
    active          BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE strategy_specs (
    spec_id         VARCHAR PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES users (user_id),
    profile_id      BIGINT NOT NULL REFERENCES risk_profiles (profile_id),
    hardcap_version VARCHAR NOT NULL REFERENCES hardcap_versions (hardcap_version),
    spec_version    VARCHAR NOT NULL,
    name            VARCHAR NOT NULL,
    input_prompt    TEXT,
    rebalance       JSONB,
    signal_rules    JSONB,
    constraint_user JSONB,
    status          VARCHAR NOT NULL CHECK (status IN ('draft', 'approved', 'running', 'closed')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE spec_universe (
    spec_universe_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    spec_id          VARCHAR NOT NULL REFERENCES strategy_specs (spec_id),
    ticker           VARCHAR NOT NULL REFERENCES etf_master (ticker),
    preset_id        BIGINT NOT NULL REFERENCES asset_bound_presets (preset_id),
    weight_min       NUMERIC,
    weight_max       NUMERIC,
    weight_min_raw   NUMERIC,
    weight_max_raw   NUMERIC,
    was_adjusted     BOOLEAN NOT NULL DEFAULT false,
    rationale        TEXT
);

CREATE TABLE validation_logs (
    validation_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    spec_id         VARCHAR NOT NULL REFERENCES strategy_specs (spec_id),
    stage           SMALLINT NOT NULL CHECK (stage BETWEEN 1 AND 4),
    passed          BOOLEAN NOT NULL,
    violations      JSONB,
    adjusted_bounds JSONB,
    clamped_fields  JSONB,
    checked_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE backtest_runs (
    run_id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    spec_id             VARCHAR NOT NULL REFERENCES strategy_specs (spec_id),
    period_start        DATE,
    period_end          DATE,
    seed_money          NUMERIC,
    fee_rate            NUMERIC,
    tax_rate            NUMERIC,
    slippage_bp         NUMERIC,
    wf_windows          INTEGER,
    random_seed         INTEGER,
    data_snapshot_asof  DATE,
    feature_set_version VARCHAR,
    status              VARCHAR,
    started_at          TIMESTAMPTZ
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

CREATE TABLE approvals (
    approval_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    spec_id       VARCHAR NOT NULL REFERENCES strategy_specs (spec_id),
    user_id       BIGINT NOT NULL REFERENCES users (user_id),
    run_id        BIGINT REFERENCES backtest_runs (run_id),
    approved      BOOLEAN NOT NULL,
    user_comment  TEXT,
    decided_at    TIMESTAMPTZ
);

CREATE TABLE portfolios (
    portfolio_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    spec_id       VARCHAR NOT NULL REFERENCES strategy_specs (spec_id),
    account_no    VARCHAR,
    mode          VARCHAR NOT NULL CHECK (mode IN ('paper', 'virtual')),
    initial_cash  NUMERIC,
    status        VARCHAR,
    activated_at  TIMESTAMPTZ
);

CREATE TABLE decision_records (
    decision_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    spec_id              VARCHAR NOT NULL REFERENCES strategy_specs (spec_id),
    portfolio_id         BIGINT NOT NULL REFERENCES portfolios (portfolio_id),
    as_of                DATE NOT NULL,
    mode                 VARCHAR NOT NULL CHECK (mode IN ('backtest', 'live')),
    integrated_signal    JSONB,
    view_weights_used    JSONB,
    risk_caps            JSONB,
    mapped_weights       JSONB,
    target_weights       JSONB,
    cash_residual        NUMERIC,
    model_version        VARCHAR,
    feature_set_version  VARCHAR,
    data_snapshot_asof   DATE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE view_weights (
    view_weight_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    portfolio_id        BIGINT NOT NULL REFERENCES portfolios (portfolio_id),
    as_of               DATE NOT NULL,
    view_type           VARCHAR NOT NULL CHECK (view_type IN ('market', 'sentiment', 'regime')),
    weight              NUMERIC NOT NULL,
    weight_floor        NUMERIC,
    update_rule_version VARCHAR,
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE positions (
    position_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    portfolio_id    BIGINT NOT NULL REFERENCES portfolios (portfolio_id),
    ticker          VARCHAR NOT NULL REFERENCES etf_master (ticker),
    quantity        INTEGER,
    avg_price       NUMERIC,
    current_weight  NUMERIC,
    as_of           DATE
);

CREATE TABLE orders (
    order_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    portfolio_id  BIGINT NOT NULL REFERENCES portfolios (portfolio_id),
    decision_id   BIGINT NOT NULL REFERENCES decision_records (decision_id),
    ticker        VARCHAR NOT NULL REFERENCES etf_master (ticker),
    side          VARCHAR NOT NULL CHECK (side IN ('buy', 'sell')),
    quantity      INTEGER NOT NULL,
    order_type    VARCHAR,
    limit_price   NUMERIC,
    status        VARCHAR,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE executions (
    execution_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id      BIGINT NOT NULL REFERENCES orders (order_id),
    filled_qty    INTEGER,
    filled_price  NUMERIC,
    fee           NUMERIC,
    tax           NUMERIC,
    executed_at   TIMESTAMPTZ
);

CREATE TABLE view_scores (
    view_score_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    decision_id       BIGINT NOT NULL REFERENCES decision_records (decision_id),
    view_type         VARCHAR NOT NULL CHECK (view_type IN ('market', 'sentiment', 'regime')),
    ticker            VARCHAR NOT NULL REFERENCES etf_master (ticker),
    raw_score         NUMERIC,
    calibrated_score  NUMERIC,
    evidence          JSONB
);

CREATE TABLE view_performance (
    perf_id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    portfolio_id  BIGINT NOT NULL REFERENCES portfolios (portfolio_id),
    decision_id   BIGINT NOT NULL REFERENCES decision_records (decision_id),
    view_type     VARCHAR NOT NULL CHECK (view_type IN ('market', 'sentiment', 'regime')),
    as_of         DATE,
    realized_at   DATE,
    hit_rate      NUMERIC,
    contribution  NUMERIC
);

CREATE TABLE group_cap_applications (
    application_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    decision_id     BIGINT NOT NULL REFERENCES decision_records (decision_id),
    group_cap_id    BIGINT NOT NULL REFERENCES group_caps (group_cap_id),
    sum_before      NUMERIC,
    cap             NUMERIC,
    scale_factor    NUMERIC
);

CREATE TABLE reports (
    report_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    decision_id   BIGINT NOT NULL REFERENCES decision_records (decision_id),
    report_type   VARCHAR NOT NULL CHECK (report_type IN ('backtest', 'daily')),
    body          TEXT,
    cited_fields  JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE audit_logs (
    audit_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_user_id   BIGINT,
    action          VARCHAR NOT NULL,
    target_type     VARCHAR NOT NULL,
    target_id       VARCHAR,
    before_value    JSONB,
    after_value     JSONB,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 데이터·지식 도메인 테이블 (기존 002_timeseries.py 에서 추출)

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
);

CREATE TABLE feature_store (
    ticker              VARCHAR NOT NULL,
    as_of               DATE NOT NULL,
    feature_set_version VARCHAR NOT NULL,
    features            JSONB,
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, as_of, feature_set_version)
);

CREATE TABLE macro_indicators (
    indicator_code VARCHAR NOT NULL,
    as_of          DATE NOT NULL,
    value          NUMERIC,
    released_at    DATE NOT NULL,
    source         VARCHAR CHECK (source IN ('ecos', 'fred', 'krx')),
    PRIMARY KEY (indicator_code, as_of)
);

CREATE TABLE regime_snapshots (
    as_of           DATE NOT NULL,
    trend_index     VARCHAR NOT NULL,
    regime_label    VARCHAR,
    intensity       NUMERIC,
    threshold_state JSONB,
    PRIMARY KEY (as_of, trend_index)
);

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
);

CREATE TABLE news_sentiment (
    sentiment_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    article_id    BIGINT NOT NULL REFERENCES news_articles (article_id),
    sector        VARCHAR,
    polarity      NUMERIC,
    confidence    NUMERIC,
    lens_id       VARCHAR,
    model_version VARCHAR
);

CREATE TABLE bbl_blocks (
    block_id      VARCHAR PRIMARY KEY,
    block_type    VARCHAR NOT NULL CHECK (block_type IN ('indicator', 'filter', 'rebalance', 'lens', 'etf_trait')),
    title         VARCHAR,
    description   TEXT,
    params_schema JSONB,
    embedding     VECTOR(768),
    active        BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE bbl_tags (
    tag_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    block_id VARCHAR NOT NULL REFERENCES bbl_blocks (block_id),
    tag      VARCHAR NOT NULL
);

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
);

-- 인덱스

CREATE UNIQUE INDEX uq_preset_lookup ON asset_bound_presets (preset_version, risk_level, risk_tag);
CREATE UNIQUE INDEX uq_view_weight ON view_weights (portfolio_id, as_of, view_type);
CREATE INDEX idx_decision_spec_asof ON decision_records (spec_id, as_of DESC);
CREATE INDEX idx_viewscore_decision ON view_scores (decision_id, view_type);
CREATE INDEX idx_groupcap_decision ON group_cap_applications (decision_id);
CREATE INDEX idx_feature_ticker_asof ON feature_store (ticker, as_of DESC);
CREATE INDEX idx_price_ticker_date ON price_daily (ticker, trade_date DESC);
CREATE INDEX idx_macro_code_asof ON macro_indicators (indicator_code, as_of DESC, released_at DESC);
CREATE INDEX idx_news_published ON news_articles (published_at DESC);
CREATE UNIQUE INDEX uq_news_dedup ON news_articles (dedup_hash);
CREATE INDEX idx_bbl_embedding ON bbl_blocks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_news_embedding ON news_articles USING hnsw (embedding vector_cosine_ops);

-- 하이퍼테이블 전환 (기존 003_hypertables.py)

SELECT create_hypertable('price_daily', 'trade_date', if_not_exists => TRUE);
SELECT create_hypertable('feature_store', 'as_of', if_not_exists => TRUE);
SELECT create_hypertable('macro_indicators', 'as_of', if_not_exists => TRUE);

-- 트리거 (기존 004_triggers.py)

CREATE FUNCTION fn_strategy_specs_block_update() RETURNS trigger AS $$
BEGIN
    IF OLD.status = 'approved' THEN
        RAISE EXCEPTION 'strategy_specs: approved 상태인 spec_id=% 는 수정할 수 없다', OLD.spec_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_strategy_specs_block_update
    BEFORE UPDATE ON strategy_specs
    FOR EACH ROW EXECUTE FUNCTION fn_strategy_specs_block_update();

CREATE FUNCTION fn_spec_universe_block_update() RETURNS trigger AS $$
DECLARE
    parent_status VARCHAR;
BEGIN
    SELECT status INTO parent_status FROM strategy_specs WHERE spec_id = OLD.spec_id;
    IF parent_status = 'approved' THEN
        RAISE EXCEPTION 'spec_universe: 상위 spec_id=% 가 approved 상태라 수정할 수 없다', OLD.spec_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_spec_universe_block_update
    BEFORE UPDATE ON spec_universe
    FOR EACH ROW EXECUTE FUNCTION fn_spec_universe_block_update();

CREATE FUNCTION fn_view_weights_block_update() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'view_weights: append-only 테이블이라 UPDATE할 수 없다 (view_weight_id=%)', OLD.view_weight_id;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_view_weights_block_update
    BEFORE UPDATE ON view_weights
    FOR EACH ROW EXECUTE FUNCTION fn_view_weights_block_update();
