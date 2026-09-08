"""core

Revision ID: 001_core
Revises:
Create Date: 2026-09-08 23:44:44.226884

전략·운용 도메인 26개 테이블. 컬럼 정의는 docs/db-erd.md 4.1을 그대로 따른다.
정본에 없는 컬럼/제약은 추가하지 않는다. 딱 하나 예외:

  * risk_profile_defaults — docs/db-erd.md에는 없는 테이블이다. 사용자가 명시적으로
    지시해서 추가했다: infra-spec.md 4.2(성향별 제약 기본값)는 애플리케이션 상수가
    아니라 (preset_version, risk_level) 키로 버전 관리되는 DB 시드여야 한다는 지시.

FK는 db-erd.md의 관계선(A ||--o{ B)에 등장하고 자식 엔티티 속성 목록에 "FK"로
표시된 컬럼에만 건다. 관계선은 있지만 속성 목록에 대응 컬럼이 없는 경우
(APPROVALS-PORTFOLIOS, VIEW_PERFORMANCE-VIEW_WEIGHTS)는 논리적 관계로만 남기고
FK 제약을 걸지 않는다 — 없는 컬럼을 지어내지 않기 위함이다.

FK 컬럼은 전부 NOT NULL이다. 유일한 예외는 asset_groups.parent_group_id로,
속성 목록 주석에 "최상위는 null"이라고 명시돼 있다. 그 외 nullability나
CHECK 제약은 db-erd.md 속성 주석에 공백으로 구분된 값 목록(예: "retail pro admin")이
명시된 경우에만 추가했다.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '001_core'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLES: list[tuple[str, str]] = [
    ("users", """
        CREATE TABLE users (
            user_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            email          VARCHAR NOT NULL,
            password_hash  VARCHAR NOT NULL,
            role           VARCHAR NOT NULL CHECK (role IN ('retail', 'pro', 'admin')),
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_users_email UNIQUE (email)
        )
    """),
    ("preset_versions", """
        CREATE TABLE preset_versions (
            preset_version VARCHAR PRIMARY KEY,
            description    TEXT,
            created_by     BIGINT NOT NULL REFERENCES users (user_id),
            activated_at   TIMESTAMPTZ,
            is_active      BOOLEAN NOT NULL DEFAULT false
        )
    """),
    ("hardcap_versions", """
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
        )
    """),
    # docs/db-erd.md에는 없는 테이블. infra-spec.md 4.2 값을 preset_version 단위로
    # 버전 관리하기 위해 사용자 지시로 추가했다 (RISK_PROFILES 생성 시 이 값을 복사해 간다).
    ("risk_profile_defaults", """
        CREATE TABLE risk_profile_defaults (
            preset_version               VARCHAR NOT NULL REFERENCES preset_versions (preset_version),
            risk_level                   SMALLINT NOT NULL CHECK (risk_level BETWEEN 1 AND 5),
            cash_min_default             NUMERIC NOT NULL,
            max_drawdown_default         NUMERIC NOT NULL,
            max_loss_per_trade_default   NUMERIC NOT NULL,
            PRIMARY KEY (preset_version, risk_level)
        )
    """),
    ("risk_profiles", """
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
        )
    """),
    ("survey_responses", """
        CREATE TABLE survey_responses (
            response_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            user_id        BIGINT NOT NULL REFERENCES users (user_id),
            question_code  VARCHAR NOT NULL,
            answer_code    VARCHAR NOT NULL,
            score          SMALLINT,
            answered_at    TIMESTAMPTZ
        )
    """),
    ("asset_bound_presets", """
        CREATE TABLE asset_bound_presets (
            preset_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            preset_version VARCHAR NOT NULL REFERENCES preset_versions (preset_version),
            risk_level     SMALLINT NOT NULL CHECK (risk_level BETWEEN 1 AND 5),
            risk_tag       VARCHAR NOT NULL,
            allowed_min    NUMERIC NOT NULL,
            allowed_max    NUMERIC NOT NULL,
            rationale      TEXT
        )
    """),
    ("asset_groups", """
        CREATE TABLE asset_groups (
            group_id         VARCHAR PRIMARY KEY,
            parent_group_id  VARCHAR REFERENCES asset_groups (group_id),
            group_level      SMALLINT NOT NULL CHECK (group_level IN (1, 2)),
            label            VARCHAR NOT NULL,
            description      TEXT
        )
    """),
    ("group_caps", """
        CREATE TABLE group_caps (
            group_cap_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            group_id         VARCHAR NOT NULL REFERENCES asset_groups (group_id),
            preset_version   VARCHAR NOT NULL REFERENCES preset_versions (preset_version),
            risk_level       SMALLINT NOT NULL CHECK (risk_level BETWEEN 0 AND 5),
            max_total_weight NUMERIC NOT NULL
        )
    """),
    ("etf_master", """
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
        )
    """),
    ("strategy_specs", """
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
        )
    """),
    ("spec_universe", """
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
        )
    """),
    ("validation_logs", """
        CREATE TABLE validation_logs (
            validation_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            spec_id         VARCHAR NOT NULL REFERENCES strategy_specs (spec_id),
            stage           SMALLINT NOT NULL CHECK (stage BETWEEN 1 AND 4),
            passed          BOOLEAN NOT NULL,
            violations      JSONB,
            adjusted_bounds JSONB,
            clamped_fields  JSONB,
            checked_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """),
    ("backtest_runs", """
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
        )
    """),
    # BACKTEST_RUNS ||--|| BACKTEST_METRICS: 1:1 식별관계. run_id가 PK이자 FK다.
    ("backtest_metrics", """
        CREATE TABLE backtest_metrics (
            run_id          BIGINT PRIMARY KEY REFERENCES backtest_runs (run_id),
            cagr            NUMERIC,
            mdd             NUMERIC,
            sharpe          NUMERIC,
            sortino         NUMERIC,
            win_rate        NUMERIC,
            benchmark_cagr  NUMERIC,
            window_results  JSONB
        )
    """),
    # APPROVALS.run_id: 속성 목록엔 FK로 표시돼 있지만 관계선이 없다.
    # (백테스트 없이 승인되는 경우를 문서가 배제하지 않는 것으로 보고) NULL 허용으로 둔다.
    ("approvals", """
        CREATE TABLE approvals (
            approval_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            spec_id       VARCHAR NOT NULL REFERENCES strategy_specs (spec_id),
            user_id       BIGINT NOT NULL REFERENCES users (user_id),
            run_id        BIGINT REFERENCES backtest_runs (run_id),
            approved      BOOLEAN NOT NULL,
            user_comment  TEXT,
            decided_at    TIMESTAMPTZ
        )
    """),
    ("portfolios", """
        CREATE TABLE portfolios (
            portfolio_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            spec_id       VARCHAR NOT NULL REFERENCES strategy_specs (spec_id),
            account_no    VARCHAR,
            mode          VARCHAR NOT NULL CHECK (mode IN ('paper', 'virtual')),
            initial_cash  NUMERIC,
            status        VARCHAR,
            activated_at  TIMESTAMPTZ
        )
    """),
    ("decision_records", """
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
        )
    """),
    # VIEW_WEIGHTS: append-only(004_triggers에서 UPDATE 차단). 여기서는 테이블만 만든다.
    ("view_weights", """
        CREATE TABLE view_weights (
            view_weight_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            portfolio_id        BIGINT NOT NULL REFERENCES portfolios (portfolio_id),
            as_of               DATE NOT NULL,
            view_type           VARCHAR NOT NULL CHECK (view_type IN ('market', 'sentiment', 'regime')),
            weight              NUMERIC NOT NULL,
            weight_floor        NUMERIC,
            update_rule_version VARCHAR,
            computed_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """),
    ("positions", """
        CREATE TABLE positions (
            position_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            portfolio_id    BIGINT NOT NULL REFERENCES portfolios (portfolio_id),
            ticker          VARCHAR NOT NULL REFERENCES etf_master (ticker),
            quantity        INTEGER,
            avg_price       NUMERIC,
            current_weight  NUMERIC,
            as_of           DATE
        )
    """),
    ("orders", """
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
        )
    """),
    ("executions", """
        CREATE TABLE executions (
            execution_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            order_id      BIGINT NOT NULL REFERENCES orders (order_id),
            filled_qty    INTEGER,
            filled_price  NUMERIC,
            fee           NUMERIC,
            tax           NUMERIC,
            executed_at   TIMESTAMPTZ
        )
    """),
    ("view_scores", """
        CREATE TABLE view_scores (
            view_score_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            decision_id       BIGINT NOT NULL REFERENCES decision_records (decision_id),
            view_type         VARCHAR NOT NULL CHECK (view_type IN ('market', 'sentiment', 'regime')),
            ticker            VARCHAR NOT NULL REFERENCES etf_master (ticker),
            raw_score         NUMERIC,
            calibrated_score  NUMERIC,
            evidence          JSONB
        )
    """),
    ("view_performance", """
        CREATE TABLE view_performance (
            perf_id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            portfolio_id  BIGINT NOT NULL REFERENCES portfolios (portfolio_id),
            decision_id   BIGINT NOT NULL REFERENCES decision_records (decision_id),
            view_type     VARCHAR NOT NULL CHECK (view_type IN ('market', 'sentiment', 'regime')),
            as_of         DATE,
            realized_at   DATE,
            hit_rate      NUMERIC,
            contribution  NUMERIC
        )
    """),
    ("group_cap_applications", """
        CREATE TABLE group_cap_applications (
            application_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            decision_id     BIGINT NOT NULL REFERENCES decision_records (decision_id),
            group_cap_id    BIGINT NOT NULL REFERENCES group_caps (group_cap_id),
            sum_before      NUMERIC,
            cap             NUMERIC,
            scale_factor    NUMERIC
        )
    """),
    ("reports", """
        CREATE TABLE reports (
            report_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            decision_id   BIGINT NOT NULL REFERENCES decision_records (decision_id),
            report_type   VARCHAR NOT NULL CHECK (report_type IN ('backtest', 'daily')),
            body          TEXT,
            cited_fields  JSONB,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """),
    # AUDIT_LOGS: db-erd.md 원문에는 4.2(데이터·지식 도메인) mermaid 블록 끝에 있지만
    # infra-spec.md 3장의 도메인 분류상 전략·운용 도메인이라 001_core에 둔다.
    # actor_user_id/target_id는 속성 목록에 FK 표시가 없어 제약을 걸지 않는다
    # (target_id는 target_type에 따라 여러 테이블을 가리키는 다형 참조라 단일 FK가 불가능하다).
    ("audit_logs", """
        CREATE TABLE audit_logs (
            audit_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            actor_user_id   BIGINT,
            action          VARCHAR NOT NULL,
            target_type     VARCHAR NOT NULL,
            target_id       VARCHAR,
            before_value    JSONB,
            after_value     JSONB,
            occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """),
]


# db-erd.md 4.4 인덱스 목록 중 이 파일이 만드는 테이블 소속인 5개.
# (feature_store/price_daily/macro_indicators/news_articles/bbl_blocks 소속 인덱스는
#  002_timeseries에, 하이퍼테이블 전환은 003_hypertables에 둔다 — 테이블과 같은
#  마이그레이션에 인덱스를 두는 게 관리하기 낫다고 판단해 4.4 원문 순서 대신 이렇게 나눴다.)
_INDEXES: list[str] = [
    "CREATE UNIQUE INDEX uq_preset_lookup ON asset_bound_presets (preset_version, risk_level, risk_tag)",
    "CREATE UNIQUE INDEX uq_view_weight ON view_weights (portfolio_id, as_of, view_type)",
    "CREATE INDEX idx_decision_spec_asof ON decision_records (spec_id, as_of DESC)",
    "CREATE INDEX idx_viewscore_decision ON view_scores (decision_id, view_type)",
    "CREATE INDEX idx_groupcap_decision ON group_cap_applications (decision_id)",
]


def upgrade() -> None:
    for _name, ddl in _TABLES:
        op.execute(ddl)
    for stmt in _INDEXES:
        op.execute(stmt)


def downgrade() -> None:
    # 인덱스는 소유 테이블에 CASCADE로 딸려 있으므로 테이블만 역순으로 지우면 된다.
    for name, _ddl in reversed(_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {name} CASCADE")
